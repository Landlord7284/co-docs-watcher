"""Identity, the state machine, and the promise that re-seeing a document changes nothing."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from co_docs_watcher.clock import Clock
from co_docs_watcher.errors import IllegalTransitionError, ManifestError
from co_docs_watcher.manifest.db import open_manifest
from co_docs_watcher.manifest.repo import (
    TRANSITIONS,
    AttemptOutcome,
    FileRecord,
    Manifest,
)
from co_docs_watcher.models import DeliveredFile, FileRole, LocalState, SourceStatus
from tests.test_models import make_document

CLOCK = Clock(ZoneInfo("America/Sao_Paulo"))
ARCHIVE = Path("2026-08-24/PETR/Fato-Relevante_160310_V01.pdf")


@pytest.fixture
def manifest(tmp_path: Path) -> Iterator[Manifest]:
    connection = open_manifest(tmp_path / "manifest.sqlite")
    yield Manifest.over(connection, CLOCK)
    connection.close()


def test_a_first_sighting_is_discovered_and_keeps_the_protocol(manifest: Manifest) -> None:
    document = make_document()
    record = manifest.documents.upsert_observed(document)

    assert record.local_state is LocalState.DISCOVERED
    assert record.identity == (160310, 1)
    # The protocol is a required download argument and cannot be derived later.
    assert record.document.protocol == document.protocol
    assert manifest.documents.require(document.identity).document == document


def test_rediscovery_updates_mutable_fields_and_leaves_local_state_alone(
    manifest: Manifest,
) -> None:
    document = make_document()
    manifest.documents.upsert_observed(document)
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    manifest.documents.transition(document.identity, LocalState.AVAILABLE, archive_path=ARCHIVE)

    # Next run: the source now calls it superseded, and says so in the same row.
    superseded = make_document(status=SourceStatus.INACTIVE, subject="Retificacao")
    record = manifest.documents.upsert_observed(superseded)

    assert record.local_state is LocalState.AVAILABLE  # never back to the download queue
    assert record.document.status is SourceStatus.INACTIVE
    assert record.document.subject == "Retificacao"
    assert record.archive_path == ARCHIVE


def test_first_seen_is_kept_and_last_seen_moves(manifest: Manifest) -> None:
    document = make_document()
    first = manifest.documents.upsert_observed(document)
    again = manifest.documents.upsert_observed(document)

    assert again.first_seen_at == first.first_seen_at
    assert again.last_seen_at >= first.last_seen_at


def test_a_resubmission_is_a_different_row(manifest: Manifest) -> None:
    manifest.documents.upsert_observed(make_document(document_id=160310))
    manifest.documents.upsert_observed(make_document(document_id=160477))
    assert len(manifest.documents.in_state(LocalState.DISCOVERED)) == 2


def test_the_same_document_at_a_new_version_is_a_different_row(manifest: Manifest) -> None:
    manifest.documents.upsert_observed(make_document(version=1))
    manifest.documents.upsert_observed(make_document(version=2))
    assert {record.identity for record in manifest.documents.in_state(LocalState.DISCOVERED)} == {
        (160310, 1),
        (160310, 2),
    }


@pytest.mark.parametrize("origin", list(LocalState))
@pytest.mark.parametrize("target", list(LocalState))
def test_the_transition_table_is_exhaustive(
    manifest: Manifest, origin: LocalState, target: LocalState
) -> None:
    document = make_document()
    manifest.documents.upsert_observed(document, initial_state=origin)
    path = ARCHIVE if target is LocalState.AVAILABLE else None
    legal = target is origin or target in TRANSITIONS[origin]

    if legal:
        record = manifest.documents.transition(document.identity, target, archive_path=path)
        assert record.local_state is target
    else:
        with pytest.raises(IllegalTransitionError, match="not a legal transition"):
            manifest.documents.transition(document.identity, target, archive_path=path)


def test_an_available_document_never_walks_back_to_the_queue(manifest: Manifest) -> None:
    # The shape of a re-download loop: available -> discovered -> download, every run.
    document = make_document()
    manifest.documents.upsert_observed(document)
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    manifest.documents.transition(document.identity, LocalState.AVAILABLE, archive_path=ARCHIVE)
    for forbidden in (LocalState.DISCOVERED, LocalState.DOWNLOADING, LocalState.SKIPPED):
        with pytest.raises(IllegalTransitionError):
            manifest.documents.transition(document.identity, forbidden)


def test_an_interrupted_download_can_be_requeued(manifest: Manifest) -> None:
    # Startup reconciliation: something was in flight when the process died.
    document = make_document()
    manifest.documents.upsert_observed(document)
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    record = manifest.documents.transition(document.identity, LocalState.DISCOVERED)
    assert record.local_state is LocalState.DISCOVERED


def test_becoming_available_requires_a_path(manifest: Manifest) -> None:
    document = make_document()
    manifest.documents.upsert_observed(document)
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    with pytest.raises(ManifestError, match="archive path"):
        manifest.documents.transition(document.identity, LocalState.AVAILABLE)


@pytest.mark.parametrize(
    "gone", [LocalState.DEACTIVATED, LocalState.CANCELLED, LocalState.PURGED]
)
def test_losing_the_file_clears_the_path(manifest: Manifest, gone: LocalState) -> None:
    document = make_document()
    manifest.documents.upsert_observed(document)
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    manifest.documents.transition(document.identity, LocalState.AVAILABLE, archive_path=ARCHIVE)
    record = manifest.documents.transition(document.identity, gone)
    assert record.archive_path is None


def test_staying_put_is_legal_and_idempotent(manifest: Manifest) -> None:
    document = make_document()
    manifest.documents.upsert_observed(document)
    record = manifest.documents.transition(document.identity, LocalState.DISCOVERED)
    assert record.local_state is LocalState.DISCOVERED


def test_purged_is_terminal() -> None:
    assert TRANSITIONS[LocalState.PURGED] == frozenset()


def test_transitioning_an_unknown_document_is_an_error(manifest: Manifest) -> None:
    with pytest.raises(ManifestError, match="not in the manifest"):
        manifest.documents.transition((1, 1), LocalState.SKIPPED)


def test_the_purge_queue_is_what_aged_out(manifest: Manifest) -> None:
    deliveries = [(1, date(2026, 8, 17)), (2, date(2026, 8, 18)), (3, date(2026, 8, 24))]
    for document_id, day in deliveries:
        manifest.documents.upsert_observed(
            make_document(document_id=document_id, delivery_date=day)
        )

    expired = manifest.documents.delivered_before(date(2026, 8, 18))
    assert [record.document.document_id for record in expired] == [1]

    manifest.documents.transition((1, 1), LocalState.PURGED)
    assert manifest.documents.delivered_before(date(2026, 8, 18)) == []


def test_a_day_can_be_read_back_for_the_inbox(manifest: Manifest) -> None:
    manifest.documents.upsert_observed(
        make_document(document_id=1, cvm_code="004170", delivery_date=date(2026, 8, 24))
    )
    manifest.documents.upsert_observed(
        make_document(document_id=2, cvm_code="009512", delivery_date=date(2026, 8, 24))
    )
    manifest.documents.upsert_observed(
        make_document(document_id=3, delivery_date=date(2026, 8, 21))
    )

    day = manifest.documents.delivered_on(date(2026, 8, 24))
    assert [record.document.cvm_code for record in day] == ["004170", "009512"]


def test_hashes_are_per_file_with_a_stability_marker(manifest: Manifest) -> None:
    document = make_document()
    manifest.documents.upsert_observed(document)
    generated = DeliveredFile(Path("/tmp/160282_009512_2408.pdf"), FileRole.GENERATED_PDF, False)
    structured = DeliveredFile(Path("/tmp/009512ITR30-06-2026v1.xml"), FileRole.MEMBER, True)

    manifest.files.record_files(
        document.identity,
        [
            FileRecord.of(
                generated,
                relative_path=Path("2026-08-24/PETR/ITR/ITR_160282_V01.pdf"),
                sha256="a" * 64,
                size_bytes=8_388_608,
            ),
            FileRecord.of(
                structured,
                relative_path=Path("2026-08-24/PETR/ITR/009512ITR30-06-2026v1.xml"),
                sha256="b" * 64,
                size_bytes=5_664_876,
            ),
        ],
    )

    recorded = manifest.files.files_for(document.identity)
    assert [file.stable for file in recorded] == [True, False]
    assert {file.role for file in recorded} == {"member", "generated_pdf"}
    assert recorded[0].sha256 == "b" * 64


def test_recording_files_again_replaces_the_set(manifest: Manifest) -> None:
    document = make_document()
    manifest.documents.upsert_observed(document)
    first = FileRecord(Path("a.pdf"), "document", "a" * 64, 10, True)
    second = FileRecord(Path("b.pdf"), "document", "b" * 64, 20, True)

    manifest.files.record_files(document.identity, [first])
    manifest.files.record_files(document.identity, [second])
    assert [file.relative_path for file in manifest.files.files_for(document.identity)] == [
        Path("b.pdf")
    ]


def test_files_of_an_unknown_document_are_refused(manifest: Manifest) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        manifest.files.record_files(
            (7, 1), [FileRecord(Path("a.pdf"), "document", "c" * 64, 1, True)]
        )


def test_attempts_are_counted_for_the_retry_budget(manifest: Manifest) -> None:
    document = make_document()
    manifest.documents.upsert_observed(document)
    manifest.attempts.record(document.identity, AttemptOutcome.FAILURE, "temErro: indisponivel")
    manifest.attempts.record(document.identity, AttemptOutcome.FAILURE, "timeout")
    manifest.attempts.record(document.identity, AttemptOutcome.SUCCESS)

    assert manifest.attempts.attempts(document.identity) == 3
    assert manifest.attempts.failures(document.identity) == 2
    assert manifest.attempts.failures((999, 1)) == 0


def test_the_watermark_records_progress(manifest: Manifest) -> None:
    assert manifest.state.watermark() is None
    manifest.state.set_watermark(date(2026, 8, 24))
    assert manifest.state.watermark() == date(2026, 8, 24)
    manifest.state.set_watermark(date(2026, 8, 25))
    assert manifest.state.watermark() == date(2026, 8, 25)


def test_arbitrary_sync_state_round_trips(manifest: Manifest) -> None:
    manifest.state.set("registry_refreshed_at", "2026-08-24")
    assert manifest.state.get("registry_refreshed_at") == "2026-08-24"
    assert manifest.state.get("absent") is None
