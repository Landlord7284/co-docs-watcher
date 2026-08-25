"""Repositories over the manifest, and the local state machine.

What is stored is ``SourceDocument`` — the neutral dataclass — plus local bookkeeping: where
the files landed, how many times we tried, and how far the sweep has got.

Three rules shape everything here:

*Identity is ``(document_id, version)``.* Every resubmission gets a new ``document_id``, so
"same id, higher version" never fires, and the content hash never dedupes: structured ZIPs are
generated on demand and hash differently on every download.

*A rediscovered document updates mutable fields and never triggers a new download.* The source
returns the whole window on every run; if re-seeing a document could walk it back to
``discovered``, the archive would re-download itself daily.

*``status`` is the source's word, ``local_state`` is ours.* ``status`` means "last state
observed at the source within the window" — it changes under us, and that is the point:
supersession and cancellation arrive as a column.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from co_docs_watcher.clock import Clock
from co_docs_watcher.errors import IllegalTransitionError, ManifestError
from co_docs_watcher.manifest.db import transaction
from co_docs_watcher.models import DeliveredFile, LocalState, SourceDocument, SourceStatus

__all__ = [
    "TRANSITIONS",
    "AttemptOutcome",
    "AttemptRepository",
    "DocumentRecord",
    "DocumentRepository",
    "FileRecord",
    "FileRepository",
    "Manifest",
    "SyncStateRepository",
]

logger = logging.getLogger(__name__)

Identity = tuple[int, int]

#: Legal moves. Staying in the same state is always legal and always a no-op.
TRANSITIONS: dict[LocalState, frozenset[LocalState]] = {
    LocalState.DISCOVERED: frozenset(
        {
            LocalState.DOWNLOADING,
            LocalState.SKIPPED,
            LocalState.DEACTIVATED,
            LocalState.CANCELLED,
            LocalState.PURGED,
        }
    ),
    # Back to ``discovered`` is startup reconciliation: an interrupted run left this in flight.
    LocalState.DOWNLOADING: frozenset(
        {
            LocalState.AVAILABLE,
            LocalState.FAILED,
            LocalState.DISCOVERED,
            LocalState.DEACTIVATED,
            LocalState.CANCELLED,
        }
    ),
    # An available document never goes back to the queue: that is the re-download loop.
    LocalState.AVAILABLE: frozenset(
        {LocalState.DEACTIVATED, LocalState.CANCELLED, LocalState.PURGED}
    ),
    # Skipped documents are re-evaluated every run: criteria change, the archive follows.
    LocalState.SKIPPED: frozenset(
        {
            LocalState.DISCOVERED,
            LocalState.DEACTIVATED,
            LocalState.CANCELLED,
            LocalState.PURGED,
        }
    ),
    LocalState.FAILED: frozenset(
        {
            LocalState.DISCOVERED,
            LocalState.DEACTIVATED,
            LocalState.CANCELLED,
            LocalState.PURGED,
        }
    ),
    # Cancellation can arrive after supersession: the source may cancel a publication it had
    # already demoted, and the inbox of the day it was observed still has to say so.
    LocalState.DEACTIVATED: frozenset(
        {LocalState.DISCOVERED, LocalState.CANCELLED, LocalState.PURGED}
    ),
    LocalState.CANCELLED: frozenset({LocalState.PURGED}),
    # Aged out of the window. The window only slides forward; nothing comes back.
    LocalState.PURGED: frozenset(),
}

#: States in which the archive is expected to hold files for the document.
_STATES_WITH_FILES = frozenset({LocalState.AVAILABLE})


class AttemptOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """A document as the manifest knows it: the source's view plus ours."""

    document: SourceDocument
    local_state: LocalState
    archive_path: Path | None
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime

    @property
    def identity(self) -> Identity:
        return self.document.identity


@dataclass(frozen=True, slots=True)
class FileRecord:
    """One file of a delivery, with the hash and the marker that says whether it is stable."""

    relative_path: Path
    role: str
    sha256: str
    size_bytes: int
    stable: bool
    recorded_at: datetime | None = None

    @classmethod
    def of(
        cls,
        delivered: DeliveredFile,
        *,
        relative_path: Path,
        sha256: str,
        size_bytes: int,
    ) -> FileRecord:
        """Build a record from a delivered file once it has been hashed and placed."""
        return cls(
            relative_path=relative_path,
            role=str(delivered.role),
            sha256=sha256,
            size_bytes=size_bytes,
            stable=delivered.stable,
        )


class _Repository:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None) -> None:
        self._connection = connection
        self._clock = clock or Clock.installed()

    def _now(self) -> str:
        return self._clock.now().isoformat(timespec="seconds")


class DocumentRepository(_Repository):
    """The documents table: discovery, lookup, and the state machine."""

    def upsert_observed(
        self, document: SourceDocument, *, initial_state: LocalState = LocalState.DISCOVERED
    ) -> DocumentRecord:
        """Record a document seen in the listing.

        First sighting inserts it in ``initial_state``. Every later sighting refreshes the
        mutable fields — status above all — and leaves ``local_state`` exactly where it was.
        ``protocol`` is written here and never derived: it is a required download argument, and
        a document discovered today cannot be downloaded tomorrow without re-listing.
        """
        existing = self.get(document.identity)
        now = self._now()
        if existing is None:
            with transaction(self._connection):
                self._connection.execute(
                    """
                    INSERT INTO documents (
                        document_id, version, protocol, cvm_code, legal_name, category,
                        doc_type, species, subject, modality, status, delivery_date,
                        reference_date, local_state, archive_path,
                        first_seen_at, last_seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        document.document_id,
                        document.version,
                        document.protocol,
                        document.cvm_code,
                        document.legal_name,
                        document.category,
                        document.doc_type,
                        document.species,
                        document.subject,
                        document.modality,
                        str(document.status),
                        document.delivery_date.isoformat(),
                        document.reference_date.isoformat() if document.reference_date else None,
                        str(initial_state),
                        now,
                        now,
                        now,
                    ),
                )
            return self.require(document.identity)

        if existing.document.delivery_date != document.delivery_date:
            # The delivery date is the archive's axis: a change would strand files in the
            # directory of the old date. Loud, because it should never happen.
            logger.warning(
                "document %s changed delivery date from %s to %s",
                document.identity,
                existing.document.delivery_date,
                document.delivery_date,
            )
        with transaction(self._connection):
            self._connection.execute(
                """
                UPDATE documents SET
                    protocol = ?, cvm_code = ?, legal_name = ?, category = ?, doc_type = ?,
                    species = ?, subject = ?, modality = ?, status = ?, delivery_date = ?,
                    reference_date = ?, last_seen_at = ?, updated_at = ?
                WHERE document_id = ? AND version = ?
                """,
                (
                    document.protocol,
                    document.cvm_code,
                    document.legal_name,
                    document.category,
                    document.doc_type,
                    document.species,
                    document.subject,
                    document.modality,
                    str(document.status),
                    document.delivery_date.isoformat(),
                    document.reference_date.isoformat() if document.reference_date else None,
                    now,
                    now,
                    document.document_id,
                    document.version,
                ),
            )
        return self.require(document.identity)

    def transition(
        self,
        identity: Identity,
        new_state: LocalState,
        *,
        archive_path: Path | None = None,
    ) -> DocumentRecord:
        """Move a document to ``new_state``, refusing anything the machine does not allow.

        ``archive_path`` is set when the document becomes ``available`` and cleared whenever it
        stops being on disk — deactivated, cancelled, purged — so the path in the manifest and
        the file in the archive never disagree.
        """
        record = self.require(identity)
        current = record.local_state
        if new_state is not current and new_state not in TRANSITIONS[current]:
            raise IllegalTransitionError(
                f"document {identity}: {current} -> {new_state} is not a legal transition"
            )
        if new_state is LocalState.AVAILABLE and archive_path is None:
            raise ManifestError(f"document {identity}: becoming available requires an archive path")

        path = str(archive_path) if archive_path is not None else None
        if new_state not in _STATES_WITH_FILES and archive_path is None:
            path = None
        elif archive_path is None:
            path = str(record.archive_path) if record.archive_path else None

        now = self._now()
        with transaction(self._connection):
            self._connection.execute(
                "UPDATE documents SET local_state = ?, archive_path = ?, updated_at = ? "
                "WHERE document_id = ? AND version = ?",
                (str(new_state), path, now, *identity),
            )
        return self.require(identity)

    def get(self, identity: Identity) -> DocumentRecord | None:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE document_id = ? AND version = ?", identity
        ).fetchone()
        return _record(row) if row is not None else None

    def require(self, identity: Identity) -> DocumentRecord:
        record = self.get(identity)
        if record is None:
            raise ManifestError(f"document {identity} is not in the manifest")
        return record

    def in_state(self, *states: LocalState) -> list[DocumentRecord]:
        placeholders = ", ".join("?" for _ in states)
        rows = self._connection.execute(
            f"SELECT * FROM documents WHERE local_state IN ({placeholders}) "
            "ORDER BY delivery_date, document_id, version",
            [str(state) for state in states],
        )
        return [_record(row) for row in rows]

    def delivered_on(self, day: date) -> list[DocumentRecord]:
        rows = self._connection.execute(
            "SELECT * FROM documents WHERE delivery_date = ? ORDER BY cvm_code, document_id",
            (day.isoformat(),),
        )
        return [_record(row) for row in rows]

    def delivered_before(self, frontier: date) -> list[DocumentRecord]:
        """Everything that has aged out of the window — the purge queue, and only that."""
        rows = self._connection.execute(
            "SELECT * FROM documents WHERE delivery_date < ? AND local_state != ? "
            "ORDER BY delivery_date, document_id",
            (frontier.isoformat(), str(LocalState.PURGED)),
        )
        return [_record(row) for row in rows]


class FileRepository(_Repository):
    """Per-file hashes. Integrity and auditing — never deduplication."""

    def record_files(self, identity: Identity, files: Iterable[FileRecord]) -> None:
        """Replace the recorded file set of a document.

        The hash is per file with a stability marker because the container is generated on
        demand: two downloads of the same ITR differ, and entry-by-entry only the generated PDF
        changes. Hashing the ZIP would record a difference that means nothing.
        """
        now = self._now()
        rows = [_file_row(identity, entry, now) for entry in files]
        with transaction(self._connection):
            self._connection.execute(
                "DELETE FROM document_files WHERE document_id = ? AND version = ?", identity
            )
            self._connection.executemany(
                "INSERT INTO document_files (document_id, version, relative_path, role, sha256, "
                "size_bytes, stable, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def files_for(self, identity: Identity) -> list[FileRecord]:
        rows = self._connection.execute(
            "SELECT * FROM document_files WHERE document_id = ? AND version = ? "
            "ORDER BY relative_path",
            identity,
        )
        return [
            FileRecord(
                relative_path=Path(row["relative_path"]),
                role=row["role"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                stable=bool(row["stable"]),
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
            )
            for row in rows
        ]


class AttemptRepository(_Repository):
    """Download attempts, which is what the retry budget is spent against."""

    def record(
        self, identity: Identity, outcome: AttemptOutcome, detail: str | None = None
    ) -> None:
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO download_attempts (document_id, version, attempted_at, outcome, "
                "detail) VALUES (?, ?, ?, ?, ?)",
                (*identity, self._now(), str(outcome), detail),
            )

    def failures(self, identity: Identity) -> int:
        row = self._connection.execute(
            "SELECT count(*) AS n FROM download_attempts WHERE document_id = ? AND version = ? "
            "AND outcome = ?",
            (*identity, str(AttemptOutcome.FAILURE)),
        ).fetchone()
        return int(row["n"])

    def attempts(self, identity: Identity) -> int:
        row = self._connection.execute(
            "SELECT count(*) AS n FROM download_attempts WHERE document_id = ? AND version = ?",
            identity,
        ).fetchone()
        return int(row["n"])


class SyncStateRepository(_Repository):
    """The watermark: completed progress and an alert baseline, never an input to the window.

    Every run sweeps the whole window regardless of what is recorded here. The watermark exists
    so that a gap — "the last completed sweep was four days ago" — can be *noticed*, not so that
    the next run can query less.
    """

    WATERMARK = "last_completed_sweep"

    def get(self, key: str) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM sync_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row is not None else None

    def set(self, key: str, value: str) -> None:
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO sync_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, self._now()),
            )

    def watermark(self) -> date | None:
        recorded = self.get(self.WATERMARK)
        return date.fromisoformat(recorded) if recorded else None

    def set_watermark(self, day: date) -> None:
        self.set(self.WATERMARK, day.isoformat())


@dataclass(frozen=True, slots=True)
class Manifest:
    """The four repositories over one connection."""

    documents: DocumentRepository
    files: FileRepository
    attempts: AttemptRepository
    state: SyncStateRepository

    @classmethod
    def over(cls, connection: sqlite3.Connection, clock: Clock | None = None) -> Manifest:
        return cls(
            documents=DocumentRepository(connection, clock),
            files=FileRepository(connection, clock),
            attempts=AttemptRepository(connection, clock),
            state=SyncStateRepository(connection, clock),
        )


def _record(row: sqlite3.Row) -> DocumentRecord:
    document = SourceDocument(
        document_id=row["document_id"],
        version=row["version"],
        protocol=row["protocol"],
        cvm_code=row["cvm_code"],
        legal_name=row["legal_name"],
        category=row["category"],
        doc_type=row["doc_type"],
        species=row["species"],
        subject=row["subject"],
        modality=row["modality"],
        status=SourceStatus(row["status"]),
        delivery_date=date.fromisoformat(row["delivery_date"]),
        reference_date=(
            date.fromisoformat(row["reference_date"]) if row["reference_date"] else None
        ),
    )
    return DocumentRecord(
        document=document,
        local_state=LocalState(row["local_state"]),
        archive_path=Path(row["archive_path"]) if row["archive_path"] else None,
        first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _file_row(identity: Identity, entry: FileRecord, now: str) -> Sequence[object]:
    return (
        *identity,
        str(entry.relative_path),
        entry.role,
        entry.sha256,
        entry.size_bytes,
        int(entry.stable),
        now,
    )
