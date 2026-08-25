"""The frontier: what exactly one window keeps, and what it takes with it when it slides."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from co_docs_watcher.clock import RetentionWindow
from co_docs_watcher.manifest.repo import FileRecord, Manifest
from co_docs_watcher.models import FileRole, LocalState, SourceDocument
from co_docs_watcher.pipeline.discover import discover
from co_docs_watcher.pipeline.purge import purge
from tests.conftest import TODAY, Roots
from tests.pipeline import PDF_BYTES, FakeSource
from tests.test_models import make_document
from tests.test_pipeline_discover import PETR


def archived(
    manifest: Manifest, roots: Roots, document: SourceDocument
) -> Path:
    """A document on disk under its delivery date, recorded and available."""
    relative = (
        Path(document.delivery_date.isoformat())
        / "PETR"
        / f"Fato-Relevante_{document.document_id}_V01.pdf"
    )
    path = roots.documents_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PDF_BYTES)
    manifest.documents.upsert_observed(document)
    manifest.files.record_files(
        document.identity,
        [FileRecord(relative, str(FileRole.DOCUMENT), "", len(PDF_BYTES), True)],
    )
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    manifest.documents.transition(
        document.identity, LocalState.AVAILABLE, archive_path=relative
    )
    return path


def index(roots: Roots, day: str) -> Path:
    roots.inbox_root.mkdir(parents=True, exist_ok=True)
    path = roots.inbox_root / f"{day}.md"
    path.write_text(f"# {day}\n", encoding="utf-8")
    return path


def run(manifest: Manifest, roots: Roots, window: RetentionWindow) -> object:
    return purge(
        manifest,
        documents_root=roots.documents_root,
        inbox_root=roots.inbox_root,
        window=window,
    )


def test_the_oldest_retained_date_survives_and_the_one_before_it_does_not(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    kept = make_document(document_id=160310, delivery_date=window.first)
    aged = make_document(document_id=160477, delivery_date=window.first - timedelta(days=1))
    kept_path = archived(manifest, roots, kept)
    aged_path = archived(manifest, roots, aged)

    outcome = run(manifest, roots, window)

    assert outcome.purged == (aged.identity,)
    assert outcome.removed_dates == (aged.delivery_date,)
    assert kept_path.exists()
    assert not aged_path.exists()
    assert manifest.documents.require(kept.identity).local_state is LocalState.AVAILABLE
    assert manifest.documents.require(aged.identity).local_state is LocalState.PURGED


def test_the_whole_date_directory_goes_with_its_contents(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    aged = make_document(delivery_date=window.first - timedelta(days=1))
    archived(manifest, roots, aged)
    expired = roots.day(aged.delivery_date)
    (expired / "VALE").mkdir(parents=True)
    (expired / "VALE" / "Aviso-aos-Acionistas_1_V01.pdf").write_bytes(PDF_BYTES)

    run(manifest, roots, window)

    assert not expired.exists()


def test_a_purged_document_forgets_the_files_it_had(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    aged = make_document(delivery_date=window.first - timedelta(days=1))
    archived(manifest, roots, aged)

    run(manifest, roots, window)

    assert manifest.files.files_for(aged.identity) == []
    assert manifest.documents.require(aged.identity).archive_path is None


def test_the_index_of_a_purged_day_goes_and_the_others_stay(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    expired = index(roots, (window.first - timedelta(days=1)).isoformat())
    inside = index(roots, window.first.isoformat())
    today = index(roots, TODAY.isoformat())

    outcome = run(manifest, roots, window)

    assert not expired.exists()
    assert inside.exists() and today.exists()
    assert outcome.removed_indexes == (window.first - timedelta(days=1),)


def test_nothing_that_is_not_a_date_directory_is_touched(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    roots.staging_root.mkdir(parents=True)
    (roots.staging_root / "160310-v1").mkdir()
    notes = roots.documents_root / "notes"
    notes.mkdir()
    (notes / "why-i-watch-these.md").write_text("mine", encoding="utf-8")

    run(manifest, roots, window)

    assert (roots.staging_root / "160310-v1").exists()
    assert (notes / "why-i-watch-these.md").exists()


def test_purged_never_means_deactivated(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    # Inside the window, a file removed because the source superseded it keeps saying so.
    document = make_document()
    archived(manifest, roots, document)
    manifest.documents.transition(document.identity, LocalState.DEACTIVATED)

    outcome = run(manifest, roots, window)

    assert outcome.purged == ()
    assert manifest.documents.require(document.identity).local_state is LocalState.DEACTIVATED


def test_a_document_that_aged_out_while_deactivated_is_still_purged(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    document = make_document(delivery_date=window.first - timedelta(days=1))
    archived(manifest, roots, document)
    manifest.documents.transition(document.identity, LocalState.DEACTIVATED)

    run(manifest, roots, window)

    assert manifest.documents.require(document.identity).local_state is LocalState.PURGED


def test_a_purged_document_is_not_resurrected_by_the_next_sweep(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    # The source still lists it — the window is what says it is over, and purge and discovery
    # read the same one.
    aged = make_document(delivery_date=window.first - timedelta(days=1))
    archived(manifest, roots, aged)
    run(manifest, roots, window)

    outcome = discover(
        FakeSource(stray=[aged]), manifest, window=window, watched=(PETR,)
    )

    assert outcome.queued == ()
    assert outcome.out_of_window == 1
    assert manifest.documents.require(aged.identity).local_state is LocalState.PURGED


def test_purging_twice_changes_nothing_the_second_time(
    manifest: Manifest, roots: Roots, window: RetentionWindow
) -> None:
    aged = make_document(delivery_date=window.first - timedelta(days=1))
    archived(manifest, roots, aged)

    first = run(manifest, roots, window)
    second = run(manifest, roots, window)

    assert first.purged == (aged.identity,)
    assert second == type(second)((), (), ())
