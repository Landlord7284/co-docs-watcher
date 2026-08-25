"""The interruption matrix, and the two flags that only reconciliation may enact."""

from __future__ import annotations

import hashlib
from pathlib import Path

from co_docs_watcher.manifest.repo import FileRecord, Manifest
from co_docs_watcher.models import FileRole, LocalState, SourceDocument, SourceStatus
from co_docs_watcher.pipeline.fetch import MAX_ATTEMPTS, fetch_pending
from co_docs_watcher.pipeline.reconcile import reconcile
from tests.conftest import TODAY, Roots
from tests.pipeline import PDF_BYTES, FakeSource
from tests.test_models import make_document
from tests.test_pipeline_discover import PETR

ARCHIVED = Path(TODAY.isoformat()) / "PETR" / "Fato-Relevante_160310_V01.pdf"


def place(roots: Roots, relative: Path, content: bytes = PDF_BYTES) -> FileRecord:
    """Write a file into the archive and describe it the way fetch would have."""
    path = roots.documents_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return FileRecord(
        relative_path=relative,
        role=str(FileRole.DOCUMENT),
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        stable=True,
    )


def in_flight(manifest: Manifest, document: SourceDocument, *files: FileRecord) -> None:
    """A document as an interrupted run left it: downloading, with whatever it got to record."""
    manifest.documents.upsert_observed(document)
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    if files:
        manifest.files.record_files(document.identity, files)


def archived(manifest: Manifest, roots: Roots, document: SourceDocument) -> None:
    """A document that finished: on disk, recorded, available."""
    record = place(roots, ARCHIVED)
    manifest.documents.upsert_observed(document)
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    manifest.files.record_files(document.identity, [record])
    manifest.documents.transition(
        document.identity, LocalState.AVAILABLE, archive_path=record.relative_path
    )


def run(manifest: Manifest, roots: Roots, **kwargs: object) -> object:
    return reconcile(
        manifest,
        documents_root=roots.documents_root,
        staging_root=roots.staging_root,
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_download_killed_in_flight_goes_back_to_the_queue(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    in_flight(manifest, document)
    debris = roots.staging_root / "160310-v1"
    debris.mkdir(parents=True)
    (debris / "document.pdf.part").write_bytes(b"%PDF-1.7\nhalf")

    outcome = run(manifest, roots)

    assert outcome.requeued == (document.identity,)
    assert manifest.documents.require(document.identity).local_state is LocalState.DISCOVERED
    assert list(roots.staging_root.iterdir()) == []
    assert not (roots.day(TODAY)).exists()


def test_a_download_killed_between_the_rename_and_the_last_write_is_recovered(
    manifest: Manifest, roots: Roots
) -> None:
    # The file rows are written after the placement and before the document is called
    # available: a complete, matching set is exactly that window.
    document = make_document()
    in_flight(manifest, document, place(roots, ARCHIVED))

    outcome = run(manifest, roots)

    record = manifest.documents.require(document.identity)
    assert outcome.recovered == (document.identity,)
    assert record.local_state is LocalState.AVAILABLE
    assert record.archive_path == ARCHIVED
    assert (roots.documents_root / ARCHIVED).exists()


def test_a_recorded_file_that_does_not_match_is_not_recovered(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    recorded = place(roots, ARCHIVED)
    in_flight(manifest, document, recorded)
    (roots.documents_root / ARCHIVED).write_bytes(b"%PDF-1.7\ntruncated")

    outcome = run(manifest, roots)

    assert outcome.requeued == (document.identity,)
    # Nothing partial stays in the archive, and nothing in the manifest points at it.
    assert not (roots.documents_root / ARCHIVED).exists()
    assert manifest.files.files_for(document.identity) == []


def test_a_recorded_file_that_vanished_is_not_recovered(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    recorded = place(roots, ARCHIVED)
    in_flight(manifest, document, recorded)
    (roots.documents_root / ARCHIVED).unlink()

    outcome = run(manifest, roots)

    assert outcome.requeued == (document.identity,)
    assert manifest.documents.require(document.identity).local_state is LocalState.DISCOVERED


def test_an_interruption_is_recorded_against_the_retry_budget(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    for _ in range(MAX_ATTEMPTS - 1):
        in_flight(manifest, document)
        run(manifest, roots)
        assert manifest.documents.require(document.identity).local_state is LocalState.DISCOVERED

    in_flight(manifest, document)
    outcome = run(manifest, roots)

    assert outcome.failed == (document.identity,)
    assert manifest.documents.require(document.identity).local_state is LocalState.FAILED


def test_a_deactivated_document_loses_its_file_and_keeps_its_row(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    archived(manifest, roots, document)
    manifest.documents.upsert_observed(make_document(status=SourceStatus.INACTIVE))
    manifest.documents.transition(document.identity, LocalState.DEACTIVATED)

    outcome = run(manifest, roots)

    record = manifest.documents.require(document.identity)
    assert outcome.enacted == (document.identity,)
    assert not (roots.documents_root / ARCHIVED).exists()
    assert record.local_state is LocalState.DEACTIVATED
    assert record.document.status is SourceStatus.INACTIVE
    assert manifest.files.files_for(document.identity) == []


def test_a_cancelled_document_keeps_the_row_the_inbox_will_mention(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    archived(manifest, roots, document)
    manifest.documents.transition(document.identity, LocalState.CANCELLED)

    run(manifest, roots)

    record = manifest.documents.require(document.identity)
    assert record.local_state is LocalState.CANCELLED
    assert record.document.delivery_date == TODAY
    assert not (roots.documents_root / ARCHIVED).exists()


def test_a_company_folder_left_empty_by_a_removal_goes_with_it(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    archived(manifest, roots, document)
    manifest.documents.transition(document.identity, LocalState.CANCELLED)

    run(manifest, roots)

    assert not (roots.day(TODAY) / "PETR").exists()
    assert not roots.day(TODAY).exists()
    assert roots.documents_root.is_dir()


def test_a_removal_leaves_the_company_folder_alone_when_the_day_still_has_documents(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    archived(manifest, roots, document)
    neighbour = roots.day(TODAY) / "PETR" / "Aviso-aos-Acionistas_160477_V01.pdf"
    neighbour.write_bytes(PDF_BYTES)
    manifest.documents.transition(document.identity, LocalState.CANCELLED)

    run(manifest, roots)

    assert neighbour.exists()


def test_reconciling_twice_changes_nothing_the_second_time(
    manifest: Manifest, roots: Roots
) -> None:
    document = make_document()
    archived(manifest, roots, document)
    manifest.documents.transition(document.identity, LocalState.DEACTIVATED)
    interrupted = make_document(document_id=160477)
    in_flight(manifest, interrupted)

    first = run(manifest, roots)
    second = run(manifest, roots)

    assert first.enacted == (document.identity,)
    assert first.requeued == (interrupted.identity,)
    assert second == type(second)((), (), (), (), 0, 0)


def test_an_orphan_in_staging_is_discarded(manifest: Manifest, roots: Roots) -> None:
    roots.staging_root.mkdir(parents=True)
    (roots.staging_root / "160310-v1").mkdir()
    (roots.staging_root / "stray.part").write_bytes(b"")

    outcome = run(manifest, roots)

    assert outcome.discarded_staging == 2
    assert list(roots.staging_root.iterdir()) == []


def test_a_finished_run_reconciles_to_itself(manifest: Manifest, roots: Roots) -> None:
    document = make_document()
    manifest.documents.upsert_observed(document)
    fetch_pending(
        FakeSource(),
        manifest,
        documents_root=roots.documents_root,
        staging_root=roots.staging_root,
        watched=(PETR,),
    )

    outcome = run(manifest, roots)

    assert outcome == type(outcome)((), (), (), (), 0, 0)
    assert manifest.documents.require(document.identity).local_state is LocalState.AVAILABLE
    assert (roots.documents_root / ARCHIVED).exists()
