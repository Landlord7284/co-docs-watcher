"""The neutral core.

Everything here is deliberately ignorant of RAD: these are the types the manifest stores and
the pipeline manipulates. The source row — twelve `$&`-separated fields, Portuguese labels,
HTML noise — never travels past ``rad/``; it is translated into ``SourceDocument`` at the
boundary and only the translation survives.

Wire-format names (``numSequencia``, ``numVersao``, ``numProtocolo``) appear only inside
``rad/``. Here they are ``document_id``, ``version`` and ``protocol``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

__all__ = [
    "DeliveredFile",
    "Delivery",
    "DeliveryKind",
    "FileRole",
    "LocalState",
    "SourceDocument",
    "SourceStatus",
]


class SourceStatus(StrEnum):
    """State of a publication *at the source*, within the queried window.

    The source marks exactly one publication per lineage ``ACTIVE`` and demotes the rest, so
    supersession is a column and never a heuristic. All three values arrive in every listing:
    status is not a server-side filter.
    """

    ACTIVE = "active"
    INACTIVE = "inactive"
    CANCELLED = "cancelled"


class LocalState(StrEnum):
    """State of a document *in this archive*.

    ``DEACTIVATED``, ``CANCELLED`` and ``PURGED`` are three different reasons for a file to
    disappear, kept apart so that ``PURGED`` keeps meaning "aged out of the window" and
    nothing else — otherwise the archive loses the ability to explain itself.
    """

    DISCOVERED = "discovered"
    DOWNLOADING = "downloading"
    AVAILABLE = "available"
    SKIPPED = "skipped"
    FAILED = "failed"
    DEACTIVATED = "deactivated"
    CANCELLED = "cancelled"
    PURGED = "purged"


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """One publication as observed in the listing.

    Identity is ``(document_id, version)`` and nothing else: every resubmission gets a new
    ``document_id``, so "same id, higher version" never fires and the content hash never
    dedupes.

    ``protocol`` is persisted rather than derived: it is a required download argument, and a
    document discovered today could not be downloaded tomorrow without it.

    ``cvm_code`` is normalized — six digits, zero-padded, hyphen stripped (the source sends
    ``00951-2``, the payload expects ``009512``).
    """

    document_id: int
    version: int
    protocol: str
    cvm_code: str
    legal_name: str
    category: str
    doc_type: str
    species: str
    subject: str
    modality: str
    status: SourceStatus
    delivery_date: date
    reference_date: date | None

    @property
    def identity(self) -> tuple[int, int]:
        """The dedupe key: ``(document_id, version)``."""
        return (self.document_id, self.version)


class DeliveryKind(StrEnum):
    """What the single download endpoint actually returned.

    Decided by the content signature, never by ``Content-Type`` — this source answers
    ``text/html`` for PDFs and ZIPs alike.
    """

    PDF = "pdf"
    ZIP = "zip"


class FileRole(StrEnum):
    """Why a file is part of a delivery.

    ``GENERATED_PDF`` is the reading copy the source builds on demand inside structured
    ZIPs: its name carries the generation instant, so the watcher imposes its own name and
    marks the file unstable.
    """

    DOCUMENT = "document"
    GENERATED_PDF = "generated_pdf"
    MEMBER = "member"


@dataclass(frozen=True, slots=True)
class DeliveredFile:
    """One file written by a download, still in the staging directory.

    ``stable`` is the marker the hash carries into the manifest: a stable file hashes the
    same on every download, an unstable one does not. It serves integrity and auditing, and
    never deduplication.
    """

    path: Path
    role: FileRole
    stable: bool


@dataclass(frozen=True, slots=True)
class Delivery:
    """The outcome of downloading one document into a staging directory.

    The container itself is not part of it: a ZIP is extracted at the boundary and never
    reaches the archive. Naming and atomic placement are the pipeline's job, not the source's.
    """

    document: SourceDocument
    kind: DeliveryKind
    files: tuple[DeliveredFile, ...]
