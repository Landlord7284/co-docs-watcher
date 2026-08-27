"""The human-facing index: what it says, what it refuses to invent, and when it changes."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from co_docs_watcher.archive_modes import ArchiveModes
from co_docs_watcher.clock import RetentionWindow
from co_docs_watcher.manifest.repo import FileRecord, Manifest
from co_docs_watcher.models import FileRole, LocalState, SourceDocument
from co_docs_watcher.pipeline.inbox import regenerate
from tests.conftest import TODAY, Roots
from tests.pipeline import PDF_BYTES
from tests.test_models import make_document


def archived(manifest: Manifest, document: SourceDocument, relative: Path) -> None:
    manifest.documents.upsert_observed(document)
    manifest.files.record_files(
        document.identity,
        [FileRecord(relative, str(FileRole.DOCUMENT), "", len(PDF_BYTES), True)],
    )
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    manifest.documents.transition(
        document.identity, LocalState.AVAILABLE, archive_path=relative
    )


def run(manifest: Manifest, roots: Roots, window: RetentionWindow) -> object:
    return regenerate(manifest, inbox_root=roots.inbox_root, window=window)


def index_of(roots: Roots, day: str = TODAY.isoformat()) -> Path:
    return roots.inbox_root / f"{day}.md"


def test_an_available_document_is_listed_with_its_subject_and_a_link(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    document = make_document()
    relative = Path(TODAY.isoformat()) / "PETR" / "Fato-Relevante_160310_V01.pdf"
    archived(manifest, document, relative)

    outcome = run(manifest, roots, window)

    text = index_of(roots).read_text(encoding="utf-8")
    assert outcome.written == (TODAY,)
    assert "# 2026-08-24" in text
    assert "## PETROLEO BRASILEIRO S.A. PETROBRAS" in text
    assert "**Fato Relevante**" in text
    assert "Petrobras informa sobre remuneracao aos acionistas" in text
    assert (
        "[Fato-Relevante_160310_V01.pdf](../2026-08-24/PETR/Fato-Relevante_160310_V01.pdf)"
    ) in text
    assert "(V01)" in text


def test_a_structured_document_with_no_subject_is_still_listed(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    document = make_document(category="ITR - Informacoes Trimestrais", subject="")
    archived(manifest, document, Path(TODAY.isoformat()) / "PETR" / "ITR")

    run(manifest, roots, window)

    line = _entries(index_of(roots))[0]
    assert line == "- **ITR - Informacoes Trimestrais** — [ITR](../2026-08-24/PETR/ITR) (V01)"


def test_a_cancellation_is_mentioned_on_the_day_it_was_observed(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    document = make_document()
    manifest.documents.upsert_observed(document)
    manifest.documents.transition(document.identity, LocalState.CANCELLED)

    run(manifest, roots, window)

    line = _entries(index_of(roots))[0]
    assert "cancelled at the source" in line
    assert "](" not in line  # no file exists, so nothing is linked


def test_a_document_that_could_not_be_fetched_is_not_passed_over_in_silence(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    document = make_document()
    manifest.documents.upsert_observed(document)
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    manifest.documents.transition(document.identity, LocalState.FAILED)

    run(manifest, roots, window)

    assert "could not be downloaded" in _entries(index_of(roots))[0]


def test_a_document_still_in_the_queue_is_not_announced(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    manifest.documents.upsert_observed(make_document())

    outcome = run(manifest, roots, window)

    assert outcome.written == ()
    assert not index_of(roots).exists()


def test_a_day_the_watcher_was_not_there_for_gets_no_index(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    document = make_document()
    archived(manifest, document, Path(TODAY.isoformat()) / "PETR" / "Fato-Relevante_160310_V01.pdf")

    run(manifest, roots, window)

    assert sorted(path.name for path in roots.inbox_root.iterdir()) == ["2026-08-24.md"]


def test_a_day_whose_documents_all_went_away_loses_its_index(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    document = make_document()
    archived(manifest, document, Path(TODAY.isoformat()) / "PETR" / "Fato-Relevante_160310_V01.pdf")
    run(manifest, roots, window)

    manifest.documents.transition(document.identity, LocalState.DEACTIVATED)
    outcome = run(manifest, roots, window)

    assert outcome.removed == (TODAY,)
    assert not index_of(roots).exists()


def test_monday_stops_pointing_at_a_file_deactivated_on_wednesday(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    monday = window.last - timedelta(days=2)
    superseded = make_document(delivery_date=monday)
    kept = make_document(document_id=160477, delivery_date=monday, subject="Ata da assembleia")
    folder = Path(monday.isoformat()) / "PETR"
    archived(manifest, superseded, folder / "Fato-Relevante_160310_V01.pdf")
    archived(manifest, kept, folder / "Fato-Relevante_160477_V01.pdf")
    run(manifest, roots, window)

    # Wednesday: the source supersedes what Monday delivered.
    manifest.documents.transition(superseded.identity, LocalState.DEACTIVATED)
    outcome = run(manifest, roots, window)

    text = index_of(roots, monday.isoformat()).read_text(encoding="utf-8")
    assert outcome.written == (monday,)
    assert "Fato-Relevante_160310_V01.pdf" not in text
    assert "Fato-Relevante_160477_V01.pdf" in text


def test_a_rewrite_that_changes_nothing_is_not_a_rewrite(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    document = make_document()
    archived(manifest, document, Path(TODAY.isoformat()) / "PETR" / "Fato-Relevante_160310_V01.pdf")
    run(manifest, roots, window)
    stamp = index_of(roots).stat().st_mtime_ns

    outcome = run(manifest, roots, window)

    assert outcome.unchanged == (TODAY,)
    assert index_of(roots).stat().st_mtime_ns == stamp


def test_companies_and_documents_come_out_in_a_stable_order(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    vale = make_document(
        document_id=160477, cvm_code="004170", legal_name="VALE S.A.", category="Aviso"
    )
    petrobras_second = make_document(document_id=160500, category="Aviso")
    petrobras_first = make_document()
    for document, relative in (
        (vale, Path(TODAY.isoformat()) / "VALE" / "Aviso_160477_V01.pdf"),
        (petrobras_second, Path(TODAY.isoformat()) / "PETR" / "Aviso_160500_V01.pdf"),
        (petrobras_first, Path(TODAY.isoformat()) / "PETR" / "Fato-Relevante_160310_V01.pdf"),
    ):
        archived(manifest, document, relative)

    run(manifest, roots, window)

    text = index_of(roots).read_text(encoding="utf-8")
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == ["## PETROLEO BRASILEIRO S.A. PETROBRAS", "## VALE S.A."]
    assert text.index("Aviso_160500_V01.pdf") < text.index("Fato-Relevante_160310_V01.pdf")


def test_only_the_days_of_the_window_are_regenerated(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    outside = window.first - timedelta(days=1)
    document = make_document(delivery_date=outside)
    archived(manifest, document, Path(outside.isoformat()) / "PETR" / "Fato-Relevante_1_V01.pdf")
    stale = roots.inbox_root / f"{outside.isoformat()}.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("# an index purge has not reached yet\n", encoding="utf-8")

    outcome = run(manifest, roots, window)

    assert outcome.written == ()
    assert stale.exists()  # outside the window is purge's business, not the inbox's


def _entries(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]


@pytest.fixture
def restrictive_umask() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.mark.usefixtures("restrictive_umask")
def test_the_index_and_its_directory_carry_the_configured_modes(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    """The index is what a reader opens first; it is created for reading, not for the umask."""
    document = make_document()
    archived(manifest, document, Path(TODAY.isoformat()) / "PETR" / "doc.pdf")

    regenerate(
        manifest,
        inbox_root=roots.inbox_root,
        window=window,
        modes=ArchiveModes(directory_mode=0o750, file_mode=0o640),
    )

    assert stat.S_IMODE(index_of(roots).stat().st_mode) == 0o640
    assert stat.S_IMODE(roots.inbox_root.stat().st_mode) == 0o750


def test_a_day_that_cannot_be_written_costs_its_own_day_and_no_other(
    manifest: Manifest, roots: Roots, window: RetentionWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A reading queue that goes dark over one bad file is worse than one day missing from it.
    yesterday = TODAY - timedelta(days=1)
    archived(manifest, make_document(), Path(TODAY.isoformat()) / "PETR" / "today.pdf")
    archived(
        manifest,
        make_document(document_id=160477, delivery_date=yesterday),
        Path(yesterday.isoformat()) / "PETR" / "yesterday.pdf",
    )
    original = os.replace

    def refuse_yesterday(source: object, target: object, *args: object, **kwargs: object):
        if str(target).endswith(f"{yesterday.isoformat()}.md"):
            raise OSError(errno.EIO, "Input/output error")
        return original(source, target, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", refuse_yesterday)

    outcome = run(manifest, roots, window)

    assert outcome.refused == (yesterday,)
    assert TODAY in outcome.written
    assert index_of(roots).exists()
    assert not index_of(roots, yesterday.isoformat()).exists()
    # ``_inbox/`` is swept by name and purge only knows ``*.md``: a leftover stays for good.
    assert list(roots.inbox_root.glob("*.part")) == []
