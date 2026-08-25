"""Purge: delete what aged out of the window, and nothing else.

``N`` counts retained dates including today, so the frontier is ``today - (N - 1)`` and it is
the *same object* discovery and the inbox read. Recomputing it here would be the classic way to
build a watcher that deletes on Tuesday what it downloads again on Wednesday, forever.

``purged`` means "aged out" and only that. A file removed because the source superseded or
cancelled the document is ``deactivated`` or ``cancelled``, enacted by reconciliation — three
different reasons for a file to be gone, kept apart so the archive can still say which one
applies.

What is deleted from disk is the *date directory*, whole. Its name is ``yyyy-mm-dd``, which is
why lexicographic order equals chronological order and why a directory that does not parse as a
date is left exactly where it is: ``.tmp/``, ``_inbox/`` and anything the operator put in the
archive are not this step's business.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from co_docs_watcher.clock import RetentionWindow, parse_directory_name
from co_docs_watcher.errors import IllegalTransitionError
from co_docs_watcher.manifest.repo import Identity, Manifest
from co_docs_watcher.models import LocalState

__all__ = ["PurgeOutcome", "purge"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PurgeOutcome:
    """What crossed the frontier."""

    purged: tuple[Identity, ...]
    removed_dates: tuple[date, ...]
    removed_indexes: tuple[date, ...]


def purge(
    manifest: Manifest,
    *,
    documents_root: Path,
    inbox_root: Path,
    window: RetentionWindow,
) -> PurgeOutcome:
    """Delete every delivery date older than the window's first, on disk and in the manifest."""
    removed_dates = _remove_expired_directories(documents_root, window)
    removed_indexes = _remove_expired_indexes(inbox_root, window)
    purged = _purge_rows(manifest, window)

    if purged or removed_dates or removed_indexes:
        logger.info(
            "purge: %d document(s), %d date directory(ies), %d index file(s) older than %s",
            len(purged),
            len(removed_dates),
            len(removed_indexes),
            window.first,
        )
    return PurgeOutcome(
        purged=tuple(purged), removed_dates=removed_dates, removed_indexes=removed_indexes
    )


def _remove_expired_directories(documents_root: Path, window: RetentionWindow) -> tuple[date, ...]:
    removed = []
    for day, path in sorted(_date_directories(documents_root)):
        if not window.is_expired(day):
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(day)
    return tuple(removed)


def _remove_expired_indexes(inbox_root: Path, window: RetentionWindow) -> tuple[date, ...]:
    """The index of a purged day goes with the day: it would point at nothing."""
    if not inbox_root.is_dir():
        return ()
    removed = []
    for path in sorted(inbox_root.glob("*.md")):
        day = _as_date(path.stem)
        if day is None or not window.is_expired(day):
            continue
        path.unlink(missing_ok=True)
        removed.append(day)
    return tuple(removed)


def _purge_rows(manifest: Manifest, window: RetentionWindow) -> list[Identity]:
    purged = []
    for record in manifest.documents.delivered_before(window.first):
        manifest.files.record_files(record.identity, ())
        try:
            manifest.documents.transition(record.identity, LocalState.PURGED)
        except IllegalTransitionError as error:
            # Only an in-flight download can be here, and reconciliation runs first: reaching
            # this means the two steps disagree about the order of a run.
            logger.error("document %s aged out but cannot be purged: %s", record.identity, error)
            continue
        purged.append(record.identity)
    return purged


def _date_directories(documents_root: Path) -> list[tuple[date, Path]]:
    """Every ``yyyy-mm-dd`` directory in the archive. Anything else is not ours to delete."""
    if not documents_root.is_dir():
        return []
    found = []
    for path in documents_root.iterdir():
        if not path.is_dir():
            continue
        day = _as_date(path.name)
        if day is not None:
            found.append((day, path))
    return found


def _as_date(name: str) -> date | None:
    try:
        return parse_directory_name(name)
    except ValueError:
        return None
