"""The reading copy of a document whose container does not carry one.

An ITR container arrives with the generated PDF inside it and an FRE container does not: the
FRE ZIP holds the structured form and ``FormularioCadastral.xml``, and nothing a person reads.
The same artifact exists at the source — it is what the listing's "Visualizar o Documento"
icon opens — but it is not behind the download endpoint. It is behind five requests, a
session, and a page that generates it on demand.

This module owns that chain and nothing else owns any part of it. What ``client.py`` lends is
transport and discipline; which pages, in which order, carrying which payloads, is stated here
once, because a chain whose shape is spread over two modules is a chain neither of them
describes.

The chain, in the order the browser walks it:

1. ``frmConsultaExternaCVM.aspx`` — the listing page. Nothing is read from it: the server
   checks that the session came in through the front door, and answers ``GerarRelatorio``
   with *"acesse este conteúdo pela página principal dos documentos"* when it did not.
2. ``frmGerenciaPaginaFRE.aspx`` — the viewer shell, which carries the hidden fields the rest
   of the chain is built from, ``hdnHash`` above all.
3. ``frmRelatorioPDF.aspx`` — the generator shell, which is where the three PageMethods live.
4. ``CarregarMenuRelatorios`` — the tree of sections, every one of them selected by default.
   It is requested rather than assumed: the sections are the document's, they differ between
   filings, and a report asked for by an invented list of identifiers is a report of something
   nobody published.
5. ``GerarRelatorio``, then ``ContinuarLeituraRelatorio`` until the answer says it is finished.

Two things the answer says about itself are not read. ``BytesLidos`` reports the size of a
fixed 16 MiB buffer rather than of the document, and ``TamanhoTotal`` reports ``0``; the
document ends at its last ``%%EOF`` and the rest of the buffer is padding. Trusting either
field would store five megabytes of NUL as though they were the filing — so the end is found
in the content, exactly as the content signature decides everything else at this boundary.

The generation is captcha-gated by a switch the source owns. ``hdnHabilitaCaptcha`` says
whether the page will attach a reCAPTCHA v3 token, and ``GerarRelatorio`` can answer ``V2:
true`` to demand an interactive one mid-chain. Either is the same news as
``SolicitarCaptcha: "S"`` in a different vocabulary and takes the same reaction: stop. There
is no legitimate workaround, and a robot that keeps asking makes the source's answer worse.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from typing import Any

from co_docs_watcher.errors import (
    CaptchaRequiredError,
    DocumentError,
    SourceContractError,
    TransientSourceError,
)
from co_docs_watcher.models import SourceDocument
from co_docs_watcher.rad.client import RadClient
from co_docs_watcher.rad.vocabulary import STRUCTURED_DOCUMENTS

__all__ = ["has_reading_page", "reading_pdf"]

logger = logging.getLogger(__name__)

#: The only category whose reading copy this build knows how to ask for. Read out of the
#: vocabulary copied from the page rather than spelled again here: the string is the source's
#: and a second transcription of it is a second thing to keep in step. The viewer page is
#: named after this category — ``frmGerenciaPaginaFRE.aspx`` — so a different category is a
#: different page and an unmeasured chain, not a parameter of this one.
_READING_PAGE_CATEGORY = STRUCTURED_DOCUMENTS["EST_2"]

_ENTRY_PATH = "frmConsultaExternaCVM.aspx"
_VIEWER_PATH = "frmGerenciaPaginaFRE.aspx"
_GENERATOR_PATH = "frmRelatorioPDF.aspx"
_MENU_METHOD = f"{_GENERATOR_PATH}/CarregarMenuRelatorios"
_GENERATE_METHOD = f"{_GENERATOR_PATH}/GerarRelatorio"
_CONTINUE_METHOD = f"{_GENERATOR_PATH}/ContinuarLeituraRelatorio"

#: The hidden fields of the viewer shell the chain is built from. Every one is required: a
#: shell that carries none of them is an error page that answered 200, which is this source's
#: way of failing, and continuing from it would spend four more requests to be told so.
_REQUIRED_FIELDS = ("hdnHash", "hdnCodigoCvm", "hdnCodigoTipoDocumento", "hdnDescricaoDocumento")

#: Read as a vocabulary of two, for the same reason ``SolicitarCaptcha`` is: the reaction to a
#: demand this build fails to recognize would be to carry on requesting.
_CAPTCHA_SWITCH = ("S", "N")

_MAGIC_PDF = b"%PDF-"
_EOF_MARKER = b"%%EOF"

#: What the buffer past the last ``%%EOF`` is allowed to contain. Measured 2026-08-28 on
#: document 161120: one ``\n`` and 5 670 272 NUL bytes. Anything else past the end of a PDF is
#: an unmeasured shape, and trimming it would be a guess about where the document stops.
_PADDING_BYTES = frozenset(b"\x00\r\n")

#: A tag carrying ``id="hdnSomething"``, whichever order its attributes arrive in.
_HIDDEN_INPUT = re.compile(r"<input\b[^>]*\bid=\"(hdn[A-Za-z0-9_]*)\"[^>]*>", re.IGNORECASE)
_VALUE_ATTRIBUTE = re.compile(r"\bvalue=\"([^\"]*)\"", re.IGNORECASE)

#: The jsTree root the menu hands back covers the whole document; ``#`` is jsTree's own
#: parent-of-the-root and names no section.
_NOT_A_SECTION = "#"

#: How many continuation reads one document is allowed. The measured document finished on the
#: first answer, so this is a fuse and never a working figure: what it stands between is a
#: source that stops advancing ``BytesLidos`` and a loop that asks forever.
_MAX_CONTINUATIONS = 64


def has_reading_page(document: SourceDocument) -> bool:
    """Whether this build knows a page that generates this document's reading copy."""
    return document.category == _READING_PAGE_CATEGORY


def reading_pdf(client: RadClient, document: SourceDocument) -> bytes:
    """Walk the chain and return the reading copy's bytes, trimmed to the document.

    Five requests at least, all of them drawing on the same minimum interval and the same
    per-run budget as the listing and the download — they are requests, and a second account
    for them would be a way to spend more of the source than the fuse believes.
    """
    label = f"document ({document.document_id}, {document.version})"
    _, entry_url = client.open_page(_ENTRY_PATH, {}, referer=None)
    viewer, viewer_url = client.open_page(
        _VIEWER_PATH,
        {
            "NumeroSequencialDocumento": str(document.document_id),
            "CodigoTipoInstituicao": "1",
        },
        referer=entry_url,
    )
    fields = _hidden_fields(viewer, label)
    _refuse_captcha_switch(fields, label)
    _, generator_url = client.open_page(
        _GENERATOR_PATH,
        {
            "CodigoTipoInstituicao": "1",
            "NumeroSequencialDocumento": str(document.document_id),
            "CodigoCVM": fields["hdnCodigoCvm"],
            "CodigoTipoDocumento": fields["hdnCodigoTipoDocumento"],
            "DescricaoTipoDocumento": fields["hdnDescricaoDocumento"],
            "PadraoCor": "A",
            "Hash": fields["hdnHash"],
        },
        referer=viewer_url,
    )
    sections = _sections(
        client.call_page_method(
            _MENU_METHOD,
            {
                "CodigoTipoDocumento": fields["hdnCodigoTipoDocumento"],
                "numeroSequencialDocumento": document.document_id,
                "Hash": fields["hdnHash"],
            },
            referer=generator_url,
        ),
        label,
    )
    logger.info("%s: the reading copy covers %d sections", label, len(sections))
    control = client.call_page_method(
        _GENERATE_METHOD,
        {
            "reportIds": ",".join(sections),
            "nrSequencialDocumento": str(document.document_id),
            "codigoCVM": fields["hdnCodigoCvm"],
            "tipoDocumento": fields["hdnDescricaoDocumento"],
            "Hash": fields["hdnHash"],
            "token": "",
            "versaoCaptcha": "",
        },
        referer=generator_url,
    )
    return _read_out(client, control, label, referer=generator_url)


def _read_out(
    client: RadClient, control: dict[str, Any], label: str, *, referer: str
) -> bytes:
    """Drain the generator's answers into one document, or say why it cannot be drained."""
    chunks: list[str] = []
    read = -1
    for _ in range(_MAX_CONTINUATIONS + 1):
        _refuse_failure(control, label)
        content = control.get("ConteudoLido")
        if content:
            chunks.append(str(content))
        if control.get("Finalizado"):
            return _document_bytes(chunks, label)
        advanced = _int_field(control, "BytesLidos")
        if advanced <= read:
            # Not finished and not further along than the answer before it: the source is no
            # longer making progress, and asking again is the shape of a loop that never ends.
            raise TransientSourceError(
                f"{label}: the reading copy stopped advancing at {advanced} bytes read"
            )
        read = advanced
        # ``ConteudoLido`` goes back empty: what is echoed is the position, and returning the
        # bytes already held would double the answer's weight for nothing.
        control = client.call_page_method(
            _CONTINUE_METHOD,
            {"controleGeracaoRelatorioPDF": {**control, "ConteudoLido": None}},
            referer=referer,
        )
    raise TransientSourceError(
        f"{label}: the reading copy did not finish within {_MAX_CONTINUATIONS} continuations"
    )


def _document_bytes(chunks: list[str], label: str) -> bytes:
    """The assembled buffer, checked and trimmed to where the document actually ends."""
    try:
        buffer = b"".join(base64.b64decode(chunk, validate=True) for chunk in chunks)
    except (binascii.Error, ValueError) as error:
        raise SourceContractError(
            f"{label}: the reading copy is not the base64 it is delivered as: {error}"
        ) from error
    if not buffer.startswith(_MAGIC_PDF):
        raise SourceContractError(
            f"{label}: the reading copy's content signature is not a PDF"
        )
    end = buffer.rfind(_EOF_MARKER)
    if end < 0:
        raise SourceContractError(
            f"{label}: the reading copy carries no {_EOF_MARKER.decode()} and this build "
            "cannot tell where the document ends"
        )
    end += len(_EOF_MARKER)
    if not set(buffer[end:]) <= _PADDING_BYTES:
        raise SourceContractError(
            f"{label}: the reading copy carries {len(buffer) - end} bytes past its last "
            f"{_EOF_MARKER.decode()} that are not padding"
        )
    logger.info(
        "%s: the reading copy is %d bytes, trimmed from a %d byte buffer",
        label,
        end,
        len(buffer),
    )
    return buffer[:end]


def _hidden_fields(html: str, label: str) -> dict[str, str]:
    """The viewer shell's hidden fields, or the reason it is not a viewer shell."""
    fields: dict[str, str] = {}
    for match in _HIDDEN_INPUT.finditer(html):
        value = _VALUE_ATTRIBUTE.search(match.group(0))
        fields[match.group(1)] = "" if value is None else value.group(1)
    missing = [name for name in _REQUIRED_FIELDS if not fields.get(name)]
    if missing:
        raise TransientSourceError(
            f"{label}: the viewer page carries none of {', '.join(missing)} — the source "
            "answered something other than the document's viewer"
        )
    return fields


def _refuse_captcha_switch(fields: dict[str, str], label: str) -> None:
    """Stop if the page would attach a captcha token, because this build has none to attach."""
    switch = fields.get("hdnHabilitaCaptcha", "")
    if switch not in _CAPTCHA_SWITCH:
        raise SourceContractError(
            f"{label}: the viewer's hdnHabilitaCaptcha is neither 'S' nor 'N': {switch!r}"
        )
    if switch == "S":
        raise CaptchaRequiredError(
            f"{label}: the source requires a captcha token to generate the reading copy; "
            "there is no legitimate workaround — turn source.fre_reading_pdf off"
        )


def _refuse_failure(control: dict[str, Any], label: str) -> None:
    """Translate the generator's two ways of saying no into the two they mean.

    ``V2`` is checked first, for the same reason the listing checks ``SolicitarCaptcha``
    before ``temErro``: if both arrive, retrying is exactly the wrong reaction.
    """
    if control.get("V2"):
        raise CaptchaRequiredError(
            f"{label}: the source demanded an interactive captcha to generate the reading "
            "copy; there is no legitimate workaround — turn source.fre_reading_pdf off"
        )
    if control.get("Erro"):
        message = control.get("MensagemErro") or "the generator answered Erro with no message"
        raise TransientSourceError(f"{label}: {message}")


def _sections(menu: Any, label: str) -> list[str]:
    """Every identifier of the section tree, root included, in the order the page walks it."""
    if isinstance(menu, str):
        # The PageMethod answers a JSON document inside a JSON string: jsTree is handed the
        # inner text verbatim, so the envelope carries it as one.
        try:
            menu = json.loads(menu)
        except ValueError as error:
            raise SourceContractError(
                f"{label}: the section tree is not JSON: {error}"
            ) from error
    identifiers: list[str] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            raise SourceContractError(f"{label}: the section tree holds a node that is not one")
        identifier = node.get("id")
        if not isinstance(identifier, str):
            raise SourceContractError(f"{label}: a section of the tree carries no identifier")
        if identifier != _NOT_A_SECTION:
            identifiers.append(identifier)
        for child in node.get("children") or ():
            walk(child)

    walk(menu)
    if not identifiers:
        raise DocumentError(f"{label}: the reading copy would cover no section at all")
    return identifiers


def _int_field(control: dict[str, Any], name: str) -> int:
    value = control.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SourceContractError(f"the generator's {name} is not an integer: {value!r}")
    return value
