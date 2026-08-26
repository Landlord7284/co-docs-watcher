"""The inbox: one Markdown index per day of the window, for a human to read.

This is the product. Everything else in the archive exists so that opening ``_inbox`` and
reading today's file answers "what was published?" in the time it takes to scan a list.

*Every* day of the window is regenerated on every run, not only today's. A document downloaded
on Monday can be deactivated on Wednesday, and Monday's index would go on pointing at a file
that is no longer there. The cost is a handful of small files rewritten; the alternative is an
index that lies about the archive it indexes.

Rewritten, never invented: an index exists for a day because the manifest holds rows for it. A
first run does not fabricate indexes for the days the watcher was not there to see, and a day
whose documents were all superseded loses its index instead of keeping an empty one.

Ordering inside a day is total and stable, so a rewrite that changes nothing produces no diff
— and a rewrite that changes nothing is not even written.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote

from co_docs_watcher.archive_modes import (
    DEFAULT_MODES,
    ArchiveModes,
    ensure_directory,
    stamp_file,
)
from co_docs_watcher.clock import RetentionWindow, directory_name
from co_docs_watcher.manifest.repo import DocumentRecord, Manifest
from co_docs_watcher.models import LocalState

__all__ = ["InboxOutcome", "regenerate", "render_day"]

logger = logging.getLogger(__name__)

#: What a day's index reports. ``AVAILABLE`` is the reading queue itself; the other two are
#: there because a document that was announced and then withdrawn, or one this watcher could
#: not fetch, is news — and silence about it reads exactly like nothing having been published.
_REPORTED = frozenset({LocalState.AVAILABLE, LocalState.CANCELLED, LocalState.FAILED})

_NOTES = {
    LocalState.CANCELLED: "cancelled at the source",
    LocalState.FAILED: "could not be downloaded",
}

_PREAMBLE = "*Regenerated from the manifest on every run — edits made here are lost.*"


@dataclass(frozen=True, slots=True)
class InboxOutcome:
    """Which indexes were rewritten, which were already right, and which stopped existing."""

    written: tuple[date, ...]
    unchanged: tuple[date, ...]
    removed: tuple[date, ...]
    entries: int


def regenerate(
    manifest: Manifest,
    *,
    inbox_root: Path,
    window: RetentionWindow,
    modes: ArchiveModes = DEFAULT_MODES,
) -> InboxOutcome:
    """Rewrite the index of every day in the window from the manifest.

    The indexes are the part of the archive most likely to be read by someone who never runs
    this program, so they are created with the archive's declared modes like everything else
    under ``documents_root``.
    """
    written: list[date] = []
    unchanged: list[date] = []
    removed: list[date] = []
    entries = 0

    for day in window.dates:
        records = [
            record
            for record in manifest.documents.delivered_on(day)
            if record.local_state in _REPORTED
        ]
        path = inbox_root / f"{directory_name(day)}.md"
        if not records:
            # Never invented: no rows, no index — and an index that outlived its rows goes.
            if path.exists():
                path.unlink()
                removed.append(day)
            continue
        entries += len(records)
        if _write_if_changed(path, render_day(day, records), modes=modes):
            written.append(day)
        else:
            unchanged.append(day)

    logger.info(
        "inbox: %d day(s) rewritten, %d unchanged, %d removed, %d entries in the window",
        len(written),
        len(unchanged),
        len(removed),
        entries,
    )
    return InboxOutcome(tuple(written), tuple(unchanged), tuple(removed), entries)


def render_day(day: date, records: list[DocumentRecord]) -> str:
    """One day's index: companies in name order, documents in a stable order under each."""
    lines = [f"# {directory_name(day)}", "", _PREAMBLE, ""]
    for company in sorted({_company(record) for record in records}):
        lines.append(f"## {company[0]}")
        lines.append("")
        for record in sorted(
            (record for record in records if _company(record) == company), key=_document_order
        ):
            lines.append(_entry(record))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _entry(record: DocumentRecord) -> str:
    """One document, on one line: what it is, what it says, and where it landed."""
    document = record.document
    parts = [f"**{document.category}**"]
    if document.subject.strip():
        parts.append(document.subject.strip())
    note = _NOTES.get(record.local_state)
    if note is not None:
        parts.append(note)
    elif record.archive_path is not None:
        parts.append(_link(record.archive_path))
    return f"- {' — '.join(parts)} ({_version(document.version)})"


def _link(archive_path: Path) -> str:
    """A relative link out of ``_inbox/`` and into the archive, next to it."""
    target = f"../{archive_path.as_posix()}"
    return f"[{archive_path.name}]({quote(target, safe='/._-')})"


def _company(record: DocumentRecord) -> tuple[str, str]:
    return (record.document.legal_name, record.document.cvm_code)


def _document_order(record: DocumentRecord) -> tuple[str, int, int]:
    document = record.document
    return (document.category, document.document_id, document.version)


def _version(version: int) -> str:
    return f"V{version:02d}"


def _write_if_changed(path: Path, content: str, *, modes: ArchiveModes) -> bool:
    """Write only a real change, and write it atomically. Returns whether anything was written.

    The archive is read while the watcher runs, so an index is replaced whole or not at all; and
    an unchanged day keeps its timestamp, which is what makes "what moved today?" answerable
    from a directory listing.
    """
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    ensure_directory(path.parent, modes)
    staging = path.with_name(path.name + ".part")
    staging.write_text(content, encoding="utf-8")
    stamp_file(staging, modes)
    os.replace(staging, path)
    return True
