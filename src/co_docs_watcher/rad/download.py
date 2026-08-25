"""Fetch one document, decide what it really is, and extract containers safely.

Nothing the response says about itself is trusted. ``Content-Type`` lies on this source —
``text/html`` for PDFs and ZIPs alike — and ``Content-Disposition`` names are useless, so
the real type comes from the content signature (``%PDF-``, ``PK\\x03\\x04``) and the header
chain (signature, then disposition, then type) never has to reach past its first link: only
two kinds exist, and anything else refuses loudly. An HTML body is rejected even when
well-formed, because the source's error page arrives with HTTP 200 and a robot that
archives it has archived an outage.

A successful parse is not enough either. ZIP members are validated before a single byte is
written — no empty containers, no ``..`` or absolute paths, a plausible root on every XML
member — and XML is inspected with the stdlib parser, which resolves no external entities.

The generated reading PDF inside a structured ZIP carries the generation instant in its
name, so it differs between two downloads of the same document: it is marked
``GENERATED_PDF`` and unstable, which is what tells the pipeline to impose its own name and
tells the manifest that its hash will not repeat. Everything else keeps its origin name and
is marked stable.
"""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from co_docs_watcher.errors import DocumentError, SourceContractError, TransientSourceError
from co_docs_watcher.models import (
    DeliveredFile,
    Delivery,
    DeliveryKind,
    FileRole,
    SourceDocument,
)
from co_docs_watcher.rad.client import RadClient

__all__ = ["MAX_EXTRACTED_BYTES", "fetch"]

_MAGIC_PDF = b"%PDF-"
_MAGIC_ZIP = b"PK\x03\x04"

#: A container with no members at all is just the end-of-central-directory record. It is
#: still a ZIP answer — one that must be rejected as an empty delivery, not mistaken for
#: an unknown signature.
_MAGIC_ZIP_EMPTY = b"PK\x05\x06"

#: What the members of one container may add up to, uncompressed. The largest measured
#: package inflates to ~14 MB; a container past this cap is a bomb, not a filing.
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024

#: The on-demand reading copy: ``{numSequencia}_{cvm code}_{generation instant}.pdf``.
_GENERATED_PDF_NAME = re.compile(r"^\d+_\d+_\d+\.pdf$", re.IGNORECASE)

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


def fetch(
    client: RadClient,
    document: SourceDocument,
    into: Path,
    *,
    max_extracted_bytes: int = MAX_EXTRACTED_BYTES,
) -> Delivery:
    """Download one document into the staging directory ``into`` and describe what landed.

    The persisted ``protocol`` is a required download argument — this is why discovery
    stores it. Naming and atomic placement in the archive belong to the caller; nothing is
    written outside ``into``.
    """
    raw = client.fetch_document(document.document_id, document.version, document.protocol)
    into.mkdir(parents=True, exist_ok=True)
    content = raw.content

    if content.startswith(_MAGIC_PDF):
        return _deliver_pdf(document, content, into)
    if content.startswith((_MAGIC_ZIP, _MAGIC_ZIP_EMPTY)):
        return _deliver_zip(document, content, into, max_extracted_bytes=max_extracted_bytes)
    if _looks_like_html(content):
        raise TransientSourceError(
            f"document ({document.document_id}, {document.version}): the source answered an "
            "HTML page instead of a document — an error page arrives with HTTP 200"
        )
    raise SourceContractError(
        f"document ({document.document_id}, {document.version}): the content signature "
        "matches nothing this build knows how to store"
    )


def _deliver_pdf(document: SourceDocument, content: bytes, into: Path) -> Delivery:
    # A neutral staging name: the archive name is imposed by the pipeline on placement.
    path = into / "document.pdf"
    path.write_bytes(content)
    file = DeliveredFile(path=path, role=FileRole.DOCUMENT, stable=True)
    return Delivery(document=document, kind=DeliveryKind.PDF, files=(file,))


def _deliver_zip(
    document: SourceDocument, content: bytes, into: Path, *, max_extracted_bytes: int
) -> Delivery:
    label = f"document ({document.document_id}, {document.version})"
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        # The signature said ZIP and the central directory disagrees: a body truncated in
        # flight looks exactly like this, and a later attempt may arrive whole.
        raise TransientSourceError(f"{label}: the ZIP cannot be read: {error}") from error

    with archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        if not members:
            raise DocumentError(f"{label}: the container is empty")
        total = sum(info.file_size for info in members)
        if total > max_extracted_bytes:
            raise DocumentError(
                f"{label}: members inflate to {total} bytes, over the "
                f"{max_extracted_bytes} byte cap"
            )
        # Everything is validated before anything is written: a delivery is whole or it
        # is nothing, and a zip-slip name must not leave even one extracted sibling.
        for info in members:
            _validate_member_name(info.filename, label)
        for info in members:
            if info.filename.lower().endswith(".xml"):
                _require_plausible_xml(archive, info, label)

        files = []
        for info in members:
            target = into / PurePosixPath(info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            generated = _GENERATED_PDF_NAME.match(PurePosixPath(info.filename).name) is not None
            files.append(
                DeliveredFile(
                    path=target,
                    role=FileRole.GENERATED_PDF if generated else FileRole.MEMBER,
                    stable=not generated,
                )
            )
    return Delivery(document=document, kind=DeliveryKind.ZIP, files=tuple(files))


def _validate_member_name(name: str, label: str) -> None:
    """Refuse anything that could write outside the staging directory."""
    posix = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or posix.is_absolute()
        or _DRIVE_LETTER.match(name)
        or ".." in posix.parts
    ):
        raise DocumentError(f"{label}: the container holds an unsafe member name: {name!r}")


def _require_plausible_xml(archive: zipfile.ZipFile, info: zipfile.ZipInfo, label: str) -> None:
    """An XML member must parse whole, with a root that is not HTML in disguise.

    A successful open is not enough: the whole member is walked, so truncation and entity
    tricks surface here instead of at whoever reads the archive later. ``ElementTree``
    resolves no external entities — a reference to one is a parse error, never a fetch —
    and finished elements are discarded as they close, so a large member is validated
    without holding its tree.
    """
    root_tag: str | None = None
    with archive.open(info) as stream:
        try:
            for event, element in ElementTree.iterparse(stream, events=("start", "end")):
                if root_tag is None:
                    root_tag = element.tag if isinstance(element.tag, str) else ""
                if event == "end":
                    element.clear()
        except ElementTree.ParseError as error:
            raise DocumentError(
                f"{label}: member {info.filename!r} is not well-formed XML: {error}"
            ) from error
    if root_tag is None:
        raise DocumentError(f"{label}: member {info.filename!r} has no XML root")
    if root_tag.rpartition("}")[2].lower() == "html":
        raise DocumentError(f"{label}: member {info.filename!r} is an HTML page, not a filing")


def _looks_like_html(content: bytes) -> bool:
    head = content.lstrip(b"\xef\xbb\xbf \t\r\n")[:256].lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html") or b"<html" in head
