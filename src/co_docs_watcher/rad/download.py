"""Fetch one document, decide what it really is, and extract containers safely.

Nothing the response says about itself is trusted, so nothing it says about itself is read:
``Content-Type`` lies on this source — ``text/html`` for PDFs and ZIPs alike — and
``Content-Disposition`` names are useless, which leaves the content signature (``%PDF-``,
``PK\\x03\\x04``) as the whole of what decides a response. Two kinds exist and anything else
refuses loudly, rather than being archived under a name a header proposed: a body this build
cannot recognize is a body it cannot vouch for, and storing it on the word of the one part of
the answer the source is measured getting wrong would be the opposite of validating it. An
HTML body is rejected even when well-formed, because the source's error page arrives with
HTTP 200 and a robot that archives it has archived an outage.

A successful parse is not enough either. ZIP members are validated before a single byte is
written — no empty containers, no ``..`` or absolute paths, a plausible root on every XML
member — and XML is inspected with the stdlib parser, which resolves no external entities.
A member that declares one encoding and arrives in another is read under the one it actually
uses instead of being refused: the declaration is the publisher's mistake, and the filing
behind it is whole. A member that cannot be read at all is named here rather than left to
travel as whatever ``zipfile`` raised: this package is where the source's failures take the
vocabulary the rest of the system decides with, and one document's bad luck must cost one
document.

The generated reading PDF inside a structured ZIP carries the generation instant in its
name, so it differs between two downloads of the same document: it is marked
``GENERATED_PDF`` and unstable, which is what tells the pipeline to impose its own name and
tells the manifest that its hash will not repeat. Everything else keeps its origin name and
is marked stable.

One container is not a structured package at all. An eventual filing delivered through the
IPE module arrives as a ZIP holding exactly two members: an envelope,
``InformacoesPeriodicasEventuais.xml``, carrying metadata the listing already gave us, and
the filing itself under a ``.ipe`` name that is a wire artifact rather than a format — the
bytes are a PDF. That package is unwrapped here: the envelope is validated, read for the
extension it declares, and discarded, and the attachment leaves as the single ``DOCUMENT``
of the delivery, so the pipeline files it exactly like the Fato Relevante that arrives as a
bare PDF. Sniffing a member is the same rule as sniffing the response, applied one layer
in: it is inside the container that this source hides a PDF behind an invented name.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import zipfile
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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

logger = logging.getLogger(__name__)

_MAGIC_PDF = b"%PDF-"
_MAGIC_ZIP = b"PK\x03\x04"

#: A container with no members at all is just the end-of-central-directory record. It is
#: still a ZIP answer — one that must be rejected as an empty delivery, not mistaken for
#: an unknown signature.
_MAGIC_ZIP_EMPTY = b"PK\x05\x06"

#: What the members of one container may add up to, uncompressed, and the fallback only: the
#: configuration file is where this is meant to be set. It is what stands between the archive
#: and a compression bomb, and it sits far past the largest measured package — ~14 MB — on
#: purpose: a cap near the real sizes would refuse a filing the day the source grows one,
#: which is a failure the archive feels and a bomb is not.
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024

#: The on-demand reading copy: ``{numSequencia}_{cvm code}_{generation instant}.pdf``.
_GENERATED_PDF_NAME = re.compile(r"^\d+_\d+_\d+\.pdf$", re.IGNORECASE)

#: The envelope member that marks a container as an IPE delivery rather than a structured
#: package. A wire name, quoted literally: it is the source's, not ours.
_IPE_ENVELOPE_NAME = "informacoesperiodicaseventuais.xml"

#: The envelope element naming the attachment's real extension — the secondary hint, used
#: only when the signature matches nothing this build recognizes.
_IPE_DECLARED_EXTENSION_ELEMENT = "ExtensaoArquivo"

#: What a declared extension is allowed to look like before it may name a file on disk. An
#: envelope is data, so it is validated rather than trusted.
_PLAUSIBLE_EXTENSION = re.compile(r"^\.[0-9A-Za-z]{1,8}$")

#: Signature to extension, for an IPE attachment. One entry, and that is the point: what an
#: eventual filing of this source turns out to be is a PDF behind an invented name, and a
#: container is not a filing. Anything else falls through to the declared hint and, failing
#: that, keeps its origin name inside the container it came in.
_ATTACHMENT_EXTENSIONS = ((_MAGIC_PDF, ".pdf"),)

#: Encoding overrides tried when reading an XML member, in order. ``None`` means "believe the
#: document's own declaration", which is the first thing tried and the usual answer. The
#: fallback exists because a member of this source can declare ``utf-8`` and arrive in
#: ISO-8859-1 — see :func:`_walk_xml_member`.
_XML_ENCODINGS: tuple[str | None, ...] = (None, "iso-8859-1")

#: How much of an XML member is fed to the parser at a time. A structured package holds
#: members of tens of megabytes, and none of them is ever held whole in memory.
_XML_CHUNK = 1024 * 1024

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
    content = client.fetch_document(document.document_id, document.version, document.protocol)
    into.mkdir(parents=True, exist_ok=True)

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
    """A container: opened, cleared whole, then either unwrapped or laid out as it stands.

    The three steps are separated because the order between them is the guarantee. Nothing
    is written while anything is still unchecked, and the question of what this container
    *is* is settled before the question of where its bytes go.
    """
    label = f"document ({document.document_id}, {document.version})"
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        # The signature said ZIP and the central directory disagrees: a body truncated in
        # flight looks exactly like this, and a later attempt may arrive whole.
        raise TransientSourceError(f"{label}: the ZIP cannot be read: {error}") from error

    with archive:
        members = _cleared_members(archive, label, max_extracted_bytes=max_extracted_bytes)
        package = _ipe_package(members)
        filing = None if package is None else _unwrapped_filing(archive, package, label)
        if filing is not None:
            return _deliver_filing(document, filing, into)
        files = _extract_members(archive, members, into, label)
    return Delivery(document=document, kind=DeliveryKind.ZIP, files=files)


def _cleared_members(
    archive: zipfile.ZipFile, label: str, *, max_extracted_bytes: int
) -> list[zipfile.ZipInfo]:
    """Every member the container declares, or an exception — never a shortened list.

    This runs before anything is written, and that is the whole reason it is a step of its
    own: a delivery is whole or it is nothing, and a zip-slip name must not leave even one
    extracted sibling behind to be found later.
    """
    members = [info for info in archive.infolist() if not info.is_dir()]
    if not members:
        raise DocumentError(f"{label}: the container is empty")
    total = sum(info.file_size for info in members)
    if total > max_extracted_bytes:
        raise DocumentError(
            f"{label}: members inflate to {total} bytes, over the "
            f"{max_extracted_bytes} byte cap"
        )
    for info in members:
        _validate_member_name(info.filename, label)
    for info in members:
        if info.filename.lower().endswith(".xml"):
            _require_plausible_xml(archive, info, label)
    return members


def _extract_members(
    archive: zipfile.ZipFile, members: list[zipfile.ZipInfo], into: Path, label: str
) -> tuple[DeliveredFile, ...]:
    """Write the members out under their origin names, marking the one that will not repeat.

    Origin names are kept because they are stable — except the reading PDF, whose name
    carries the instant it was generated. It is marked rather than renamed here: the archive
    name is the pipeline's to impose, and the marker is also what tells the manifest that
    this file's hash is not evidence of anything.
    """
    files = []
    for info in members:
        target = into / PurePosixPath(info.filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        with (
            _member_read(label, info.filename),
            archive.open(info) as source,
            target.open("wb") as sink,
        ):
            shutil.copyfileobj(source, sink)
        generated = _GENERATED_PDF_NAME.match(PurePosixPath(info.filename).name) is not None
        files.append(
            DeliveredFile(
                path=target,
                role=FileRole.GENERATED_PDF if generated else FileRole.MEMBER,
                stable=not generated,
            )
        )
    return tuple(files)


@dataclass(frozen=True, slots=True)
class _IpePackage:
    """A container that carries the IPE envelope: the envelope, and everything else."""

    envelope: zipfile.ZipInfo
    attachments: tuple[zipfile.ZipInfo, ...]


@dataclass(frozen=True, slots=True)
class _IpeFiling:
    """The single filing an IPE container wraps, and the extension it may be named by."""

    content: bytes
    extension: str


def _ipe_package(members: list[zipfile.ZipInfo]) -> _IpePackage | None:
    """The container split around the IPE envelope, or ``None``: it carries none.

    One question and one meaning for the answer. A container without the envelope is a
    structured package — a different delivery, not this code's business, and nothing about
    it is irregular enough to say anything about.
    """
    envelope: zipfile.ZipInfo | None = None
    attachments: list[zipfile.ZipInfo] = []
    for info in members:
        if PurePosixPath(info.filename).name.lower() == _IPE_ENVELOPE_NAME:
            envelope = info
        else:
            attachments.append(info)
    if envelope is None:
        return None
    return _IpePackage(envelope=envelope, attachments=tuple(attachments))


def _unwrapped_filing(
    archive: zipfile.ZipFile, package: _IpePackage, label: str
) -> _IpeFiling | None:
    """The one filing this container reduces to, or ``None``: it does not reduce.

    ``None`` here says the opposite of the other one — this *is* an IPE container and this
    build cannot reduce it. The measured shape is the envelope plus exactly one attachment;
    any other count is a shape nobody has measured, and an attachment whose bytes match no
    signature and whose envelope declares no usable extension is a file that cannot be
    named. Both are said out loud and both leave the container whole: discarding the
    envelope is the one irreversible move here, and it is not made on a hunch.
    """
    if len(package.attachments) != 1:
        logger.warning(
            "%s: an IPE envelope with %d attachment(s) is a shape this build has not "
            "measured; the container is being archived whole",
            label,
            len(package.attachments),
        )
        return None

    attachment = package.attachments[0]
    with _member_read(label, attachment.filename):
        content = archive.read(attachment)
    if content.startswith((_MAGIC_ZIP, _MAGIC_ZIP_EMPTY)):
        # Reducing this would put a container in the archive as the delivery's one document,
        # with none of the checks a container gets — no member names, no inflation cap, no
        # XML looked at. Archived whole instead: the nesting stays visible to whoever opens it.
        logger.warning(
            "%s: the IPE attachment %r is itself a container, a shape this build has not "
            "measured; the container is being archived whole",
            label,
            attachment.filename,
        )
        return None
    declared = _declared_extension(archive, package.envelope, label)
    extension = _attachment_extension(content, declared)
    if extension is None:
        logger.warning(
            "%s: the IPE attachment %r matches no signature this build knows and the "
            "envelope declares no usable extension; the container is being archived whole",
            label,
            attachment.filename,
        )
        return None
    return _IpeFiling(content=content, extension=extension)


def _deliver_filing(document: SourceDocument, filing: _IpeFiling, into: Path) -> Delivery:
    """Write the unwrapped filing out as the single file of the delivery.

    A neutral staging name, as for a bare PDF: the archive name is the pipeline's to impose,
    and the origin name — CVM code, dates and protocol run together — names nothing a human
    reads. The envelope does not come along; it carried metadata the listing already gave us.
    """
    path = into / f"document{filing.extension}"
    path.write_bytes(filing.content)
    file = DeliveredFile(path=path, role=FileRole.DOCUMENT, stable=True)
    return Delivery(document=document, kind=DeliveryKind.ZIP, files=(file,))


def _attachment_extension(content: bytes, declared: str | None) -> str | None:
    """The attachment's real extension: signature first, the envelope's word only after."""
    for magic, extension in _ATTACHMENT_EXTENSIONS:
        if content.startswith(magic):
            return extension
    return declared


def _declared_extension(
    archive: zipfile.ZipFile, envelope: zipfile.ZipInfo, label: str
) -> str | None:
    """``ExtensaoArquivo`` from the envelope, if it is something a file may be named with.

    Read on the same walk every XML member gets, which is what keeps one answer to "how is
    an XML member of this source read" instead of two that could drift apart. Silent about
    the encoding, though: the envelope was validated before this runs, so whatever was worth
    saying about how it reads has been said once already.

    An envelope that declares nothing usable is a missing hint and never a failed delivery —
    the attachment then falls back to its signature, and failing that the container is
    archived whole, envelope included.
    """
    walk = _walk_xml_member(archive, envelope, label, target=_DeclaredExtension)
    declared = (walk.kept or "").strip().lower()
    return declared if _PLAUSIBLE_EXTENSION.match(declared) else None


@contextmanager
def _member_read(label: str, filename: str) -> Iterator[None]:
    """Name what a member read can fail with, in the vocabulary the pipeline knows.

    Opening the container already translates :class:`zipfile.BadZipFile`, but a member is
    barely examined at that point: its local header, its CRC and its cipher are checked when
    it is read, so the same body that opened cleanly fails one layer in. The three ways it
    does are neither one exception type nor one severity — a truncated or corrupt member is
    the source failing under load and may arrive whole on the next attempt, while an
    encrypted member is a delivery this build cannot store and will not be able to store
    later — and none of the three descends from anything the pipeline catches. Escaping
    unnamed, they cost the run every step that had not happened yet: an isolated failure
    that kills the batch is exactly what this project's error hierarchy exists to prevent.
    """
    try:
        yield
    except (zipfile.BadZipFile, zlib.error) as error:
        raise TransientSourceError(
            f"{label}: member {filename!r} could not be read: {error}"
        ) from error
    except RuntimeError as error:
        raise DocumentError(
            f"{label}: member {filename!r} could not be extracted: {error}"
        ) from error


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


class _RootTag:
    """A parser target that walks a member without building anything from it.

    Only the first tag is kept. What validation asks of an XML member is whether it parses
    from end to end and whether its root is a filing rather than an HTML page in disguise,
    and neither question needs the elements that already closed — so none are retained, and
    a member of tens of megabytes is validated in constant memory.
    """

    def __init__(self) -> None:
        self.root_tag: str | None = None

    def start(self, tag: str, attrib: dict[str, str]) -> None:
        if self.root_tag is None:
            self.root_tag = tag

    def end(self, tag: str) -> None:
        """Required by the parser target protocol; a closed element is nothing to us."""

    def data(self, data: str) -> None:
        """Required by the parser target protocol; text is nothing to us."""

    def close(self) -> str | None:
        return self.root_tag


@dataclass(frozen=True, slots=True)
class _XmlWalk:
    """One completed walk of an XML member: what the target kept, and how it had to be read.

    ``fallback_encoding`` is ``None`` when the document's own declaration was true, which is
    the usual answer. It travels back to the caller rather than being reported here, because
    a member walked twice is read twice and mis-declared once: the walk knows the fact, and
    only validation is in a position to say it a single time.
    """

    kept: str | None
    fallback_encoding: str | None


class _DeclaredExtension:
    """A parser target that keeps the first ``ExtensaoArquivo`` text and nothing else.

    The same shape as :class:`_RootTag`, and for the same reason: the envelope is small, but
    it is read by the walk every member is read by, and that walk holds nothing. The first
    declaration wins — a second one would be a shape nobody has measured, and taking the
    later of two answers is not a way to resolve them.
    """

    def __init__(self) -> None:
        self._collecting = False
        self._parts: list[str] = []
        self._declared: str | None = None

    def start(self, tag: str, attrib: dict[str, str]) -> None:
        self._collecting = (
            self._declared is None
            and tag.rpartition("}")[2] == _IPE_DECLARED_EXTENSION_ELEMENT
        )

    def end(self, tag: str) -> None:
        if self._collecting:
            self._declared = "".join(self._parts)
            self._collecting = False

    def data(self, data: str) -> None:
        if self._collecting:
            self._parts.append(data)

    def close(self) -> str | None:
        return self._declared


def _require_plausible_xml(archive: zipfile.ZipFile, info: zipfile.ZipInfo, label: str) -> None:
    """An XML member must parse whole, with a root that is not HTML in disguise.

    A successful open is not enough: the whole member is walked, so truncation and entity
    tricks surface here instead of at whoever reads the archive later. ``ElementTree``
    resolves no external entities — a reference to one is a parse error, never a fetch.
    """
    walk = _walk_xml_member(archive, info, label, target=_RootTag)
    if walk.fallback_encoding is not None:
        logger.warning(
            "%s: member %r declares an encoding it does not use and was read as %s "
            "instead; it is archived exactly as delivered",
            label,
            info.filename,
            walk.fallback_encoding,
        )
    root_tag = walk.kept
    if root_tag is None:
        raise DocumentError(f"{label}: member {info.filename!r} has no XML root")
    if root_tag.rpartition("}")[2].lower() == "html":
        raise DocumentError(f"{label}: member {info.filename!r} is an HTML page, not a filing")


def _walk_xml_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    label: str,
    *,
    target: Callable[[], object],
) -> _XmlWalk:
    """Parse one member from end to end and return whatever ``target`` kept of it.

    Two things are asked of an XML member of this source — whether it parses at all, and
    what the IPE envelope declares its attachment to be — and they are one walk with two
    targets rather than two readers: the encoding rule below is the whole reason, since a
    second reader is a second place for it to be decided differently.

    The declaration is believed first and the parse is retried under ISO-8859-1 when it
    fails, because a member of this source can say ``encoding="utf-8"`` and deliver
    ISO-8859-1: the first accented letter of a company name is then an invalid token, and a
    filing that is whole, readable and correct in every other respect would be refused over
    a header the publisher wrote wrong. The retry is narrow by construction — ISO-8859-1
    decodes every byte, so a member that still fails to parse is malformed in structure and
    not merely mislabelled, and the error reported is the first one, under the encoding the
    document itself claimed.

    Nothing is rewritten: the bytes reaching the archive are the bytes delivered, wrong
    declaration included. This decides whether a member may be stored, never what it says.
    """
    first: ElementTree.ParseError | None = None
    for encoding in _XML_ENCODINGS:
        try:
            kept = _walk_under(archive, info, label, target=target, encoding=encoding)
        except ElementTree.ParseError as error:
            if first is None:
                first = error
            continue
        return _XmlWalk(kept=kept, fallback_encoding=encoding)
    raise DocumentError(
        f"{label}: member {info.filename!r} is not well-formed XML: {first}"
    ) from first


def _walk_under(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    label: str,
    *,
    target: Callable[[], object],
    encoding: str | None,
) -> str | None:
    """One pass over one member under one encoding: what the target kept, or the parse error.

    ``encoding=None`` is what believes the document's own declaration, and the chunk at a
    time is what keeps a member of tens of megabytes out of memory — every target here
    retains a single string, so there is nothing to accumulate as the walk goes on.
    """
    parser = ElementTree.XMLParser(target=target(), encoding=encoding)
    with _member_read(label, info.filename), archive.open(info) as stream:
        while chunk := stream.read(_XML_CHUNK):
            parser.feed(chunk)
        return parser.close()


def _looks_like_html(content: bytes) -> bool:
    """Whether the body is a page rather than a document — on this source, an error page.

    The body has to *begin* as markup before its first bytes are searched for an ``<html``.
    The error page does begin that way, sometimes behind a comment or an XML prolog, while a
    format this build does not recognize can carry those five bytes anywhere in its header.
    Reading one as the other answers contract divergence — which needs a person to look at
    it — with a backoff and three retries that cannot help, and buries the news.
    """
    head = content.lstrip(b"\xef\xbb\xbf \t\r\n")[:256].lower()
    return head.startswith(b"<") and (head.startswith(b"<!doctype") or b"<html" in head)
