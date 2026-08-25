"""Reconciliation: everything a previous run may have left half-done, plus enacting flags.

Two jobs meet here because they are the same job. The filesystem and SQLite do not form one
transaction, so the manifest is the only memory of what was supposed to happen, and this step
is where it is compared against what actually did — for a download killed in flight, and for a
document the source has since superseded or cancelled.

It runs at the *start* of a run, before anything is discovered or fetched, so that a run
inherits a consistent archive rather than adding to a broken one — and :func:`enact_flags` runs
again right after the sweep, so that a cancellation observed today takes the file with it today
rather than leaving one run in which the index says cancelled and the file is still there.
Nothing is lost if that second call never happens: the flag is the document's state and not
something held in memory, so the next run enacts it.

Everything here is idempotent. Running it twice changes nothing the second time, which is the
only property that makes it safe to run before every single run.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from co_docs_watcher.manifest.repo import (
    AttemptOutcome,
    DocumentRecord,
    FileRecord,
    Identity,
    Manifest,
)
from co_docs_watcher.models import LocalState
from co_docs_watcher.pipeline.fetch import MAX_ATTEMPTS, archive_path_of, sha256_of

__all__ = ["EnactedFlags", "ReconcileOutcome", "enact_flags", "reconcile"]

logger = logging.getLogger(__name__)

#: States that mean "the file is gone but the row explains why". The row is kept, and it is
#: what the inbox reads to mention a cancellation on the day it was observed.
_FLAGGED = (LocalState.DEACTIVATED, LocalState.CANCELLED)


@dataclass(frozen=True, slots=True)
class EnactedFlags:
    """The documents whose files were removed because the source withdrew them."""

    identities: tuple[Identity, ...]
    removed_files: int


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """What was left half-done, and what became of it."""

    recovered: tuple[Identity, ...]
    requeued: tuple[Identity, ...]
    failed: tuple[Identity, ...]
    enacted: tuple[Identity, ...]
    removed_files: int
    discarded_staging: int


def reconcile(
    manifest: Manifest,
    *,
    documents_root: Path,
    staging_root: Path,
    max_attempts: int = MAX_ATTEMPTS,
) -> ReconcileOutcome:
    """Bring the archive and the manifest back into agreement."""
    recovered: list[Identity] = []
    requeued: list[Identity] = []
    failed: list[Identity] = []
    removed_files = 0

    for record in manifest.documents.in_state(LocalState.DOWNLOADING):
        outcome, removed = _resolve_in_flight(
            manifest, record, documents_root=documents_root, max_attempts=max_attempts
        )
        removed_files += removed
        {
            LocalState.AVAILABLE: recovered,
            LocalState.DISCOVERED: requeued,
            LocalState.FAILED: failed,
        }[outcome].append(record.identity)

    flags = enact_flags(manifest, documents_root=documents_root)
    enacted = list(flags.identities)
    removed_files += flags.removed_files

    discarded = _discard_staging(staging_root)

    if recovered or requeued or failed or enacted or discarded:
        logger.info(
            "reconciliation: %d recovered, %d requeued, %d failed, %d flags enacted, "
            "%d staging leftovers discarded",
            len(recovered),
            len(requeued),
            len(failed),
            len(enacted),
            discarded,
        )
    return ReconcileOutcome(
        recovered=tuple(recovered),
        requeued=tuple(requeued),
        failed=tuple(failed),
        enacted=tuple(enacted),
        removed_files=removed_files,
        discarded_staging=discarded,
    )


def enact_flags(manifest: Manifest, *, documents_root: Path) -> EnactedFlags:
    """Remove what deactivated and cancelled documents left in the archive.

    Called at the start of a run for flags inherited from the previous one, and again right
    after the sweep for the flags it just raised. Idempotent both times: a document whose files
    are already gone has no file rows left to act on.
    """
    identities: list[Identity] = []
    removed_files = 0
    for record in manifest.documents.in_state(*_FLAGGED):
        removed = _enact_flag(manifest, record, documents_root=documents_root)
        if removed:
            identities.append(record.identity)
            removed_files += removed
    return EnactedFlags(tuple(identities), removed_files)


def _resolve_in_flight(
    manifest: Manifest,
    record: DocumentRecord,
    *,
    documents_root: Path,
    max_attempts: int,
) -> tuple[LocalState, int]:
    """Decide what a download that was in flight when the process died actually achieved.

    The recorded files are the evidence, not the state on disk alone: they are written after
    the placement and before the document is called available, so a complete, matching set is
    exactly the case where the rename went through and only the last write did not.
    """
    identity = record.identity
    files = manifest.files.files_for(identity)
    if files and _intact(files, documents_root=documents_root):
        manifest.documents.transition(
            identity, LocalState.AVAILABLE, archive_path=archive_path_of(files)
        )
        manifest.attempts.record(identity, AttemptOutcome.SUCCESS, "recovered at startup")
        logger.info("document %s was already on disk when the run died; recovered", identity)
        return LocalState.AVAILABLE, 0

    removed = _remove_recorded_files(manifest, record, documents_root=documents_root)
    manifest.attempts.record(identity, AttemptOutcome.FAILURE, "interrupted while downloading")
    exhausted = manifest.attempts.failures(identity) >= max_attempts
    target = LocalState.FAILED if exhausted else LocalState.DISCOVERED
    manifest.documents.transition(identity, target)
    logger.warning("document %s was interrupted while downloading; now %s", identity, target)
    return target, removed


def _enact_flag(manifest: Manifest, record: DocumentRecord, *, documents_root: Path) -> int:
    """Remove what a deactivated or cancelled document left in the archive.

    The row stays exactly where it is. ``deactivated`` and ``cancelled`` are kept apart from
    ``purged`` precisely so the archive can still explain why a file is not there any more, and
    a cancellation is mentioned in the inbox of the day it was observed even though no file of
    it exists.
    """
    removed = _remove_recorded_files(manifest, record, documents_root=documents_root)
    if removed:
        logger.info(
            "document %s is %s at the source; removed %d file(s) from the archive",
            record.identity,
            record.document.status,
            removed,
        )
    return removed


def _intact(files: list[FileRecord], *, documents_root: Path) -> bool:
    """Whether every recorded file is on disk, whole, and the one that was recorded."""
    for entry in files:
        path = documents_root / entry.relative_path
        if not path.is_file() or path.stat().st_size != entry.size_bytes:
            return False
        if sha256_of(path) != entry.sha256:
            return False
    return True


def _remove_recorded_files(
    manifest: Manifest, record: DocumentRecord, *, documents_root: Path
) -> int:
    """Delete the files a document left behind and forget them. Safe to repeat."""
    files = manifest.files.files_for(record.identity)
    if not files:
        return 0
    removed = 0
    for entry in files:
        path = documents_root / entry.relative_path
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except OSError as error:
            logger.error("document %s: %s cannot be removed: %s", record.identity, path, error)
            continue
        _prune_empty_parents(path.parent, documents_root=documents_root)
    manifest.files.record_files(record.identity, ())
    return removed


def _prune_empty_parents(directory: Path, *, documents_root: Path) -> None:
    """Walk up while the directories are empty. A day nobody published on keeps no folder."""
    current = directory.resolve()
    root = documents_root.resolve()
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _discard_staging(staging_root: Path) -> int:
    """Empty ``.tmp/``. Everything in it is by definition an unfinished download.

    The lock guarantees no other run is holding a file there: a leftover is debris from a run
    that is no longer alive, and keeping it would only grow the archive's least useful folder.
    """
    if not staging_root.is_dir():
        return 0
    discarded = 0
    for entry in staging_root.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        discarded += 1
    return discarded
