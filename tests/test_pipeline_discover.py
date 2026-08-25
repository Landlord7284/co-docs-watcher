"""What one sweep does to the manifest, and everything it deliberately does not do."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pytest

from co_docs_watcher.clock import RetentionWindow
from co_docs_watcher.cvm.search import MatchKind
from co_docs_watcher.cvm.ticker import PrefixSource
from co_docs_watcher.manifest.repo import Manifest
from co_docs_watcher.models import LocalState, SourceDocument, SourceStatus
from co_docs_watcher.pipeline.discover import discover
from co_docs_watcher.scope.models import WatchedCompany
from tests.conftest import TODAY
from tests.pipeline import FakeSource
from tests.test_models import make_document

PETR = WatchedCompany(
    cvm_code="009512",
    prefix="PETR",
    prefix_source=PrefixSource.TICKER,
    legal_name="PETROLEO BRASILEIRO S.A. PETROBRAS",
    matched_by=MatchKind.TICKER,
)
VALE = WatchedCompany(
    cvm_code="004170",
    prefix="VALE",
    prefix_source=PrefixSource.TICKER,
    legal_name="VALE S.A.",
    matched_by=MatchKind.TICKER,
)


def archived_at(document: SourceDocument) -> Path:
    """Where a fetched document would have landed. Discovery never writes it; it only flags."""
    return Path(TODAY.isoformat()) / "PETR" / f"Fato-Relevante_{document.document_id}_V01.pdf"


def sweep(
    manifest: Manifest,
    window: RetentionWindow,
    *documents: SourceDocument,
    watched: tuple[WatchedCompany, ...] = (PETR,),
    **kwargs: object,
) -> object:
    source = FakeSource(documents)
    return discover(source, manifest, window=window, watched=watched, **kwargs)  # type: ignore[arg-type]


def test_a_new_active_document_is_queued_with_its_protocol(
    manifest: Manifest, window: RetentionWindow
) -> None:
    document = make_document()
    outcome = sweep(manifest, window, document)

    record = manifest.documents.require(document.identity)
    assert outcome.queued == (document.identity,)
    assert record.local_state is LocalState.DISCOVERED
    # Without the protocol the document could not be downloaded tomorrow without re-listing.
    assert record.document.protocol == document.protocol


def test_the_sweep_asks_for_every_day_of_the_window_once(
    manifest: Manifest, window: RetentionWindow
) -> None:
    source = FakeSource([make_document()])
    discover(source, manifest, window=window, watched=(PETR,))

    assert source.requested == [window.dates]
    assert len(window.dates) == 7


def test_rows_of_unwatched_companies_are_counted_and_dropped(
    manifest: Manifest, window: RetentionWindow
) -> None:
    watched = make_document()
    stranger = make_document(document_id=999001, cvm_code="002437")

    outcome = sweep(manifest, window, watched, stranger)

    assert (outcome.observed, outcome.ignored) == (2, 1)
    assert manifest.documents.get(stranger.identity) is None


def test_rediscovery_does_not_send_an_available_document_back_to_the_queue(
    manifest: Manifest, window: RetentionWindow
) -> None:
    document = make_document()
    sweep(manifest, window, document)
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    manifest.documents.transition(
        document.identity, LocalState.AVAILABLE, archive_path=archived_at(document)
    )

    outcome = sweep(manifest, window, make_document(subject="Retificacao"))

    record = manifest.documents.require(document.identity)
    assert outcome.queued == ()
    assert outcome.unchanged == 1
    assert record.local_state is LocalState.AVAILABLE
    assert record.document.subject == "Retificacao"  # mutable fields still follow the source


def test_an_available_document_gone_inactive_is_flagged_deactivated(
    manifest: Manifest, window: RetentionWindow
) -> None:
    document = make_document()
    sweep(manifest, window, document)
    manifest.documents.transition(document.identity, LocalState.DOWNLOADING)
    manifest.documents.transition(
        document.identity, LocalState.AVAILABLE, archive_path=archived_at(document)
    )

    outcome = sweep(manifest, window, make_document(status=SourceStatus.INACTIVE))

    record = manifest.documents.require(document.identity)
    assert outcome.deactivated == (document.identity,)
    assert record.local_state is LocalState.DEACTIVATED
    assert record.document.status is SourceStatus.INACTIVE
    # The file removal is reconciliation's job; discovery only flags.
    assert record.archive_path is None


def test_a_cancelled_document_is_flagged_and_keeps_the_day_it_was_delivered(
    manifest: Manifest, window: RetentionWindow
) -> None:
    document = make_document()
    sweep(manifest, window, document)

    outcome = sweep(manifest, window, make_document(status=SourceStatus.CANCELLED))

    record = manifest.documents.require(document.identity)
    assert outcome.cancelled == (document.identity,)
    assert record.local_state is LocalState.CANCELLED
    assert record.document.delivery_date == document.delivery_date


def test_a_cancellation_after_a_supersession_is_still_recorded(
    manifest: Manifest, window: RetentionWindow
) -> None:
    document = make_document()
    sweep(manifest, window, document)
    sweep(manifest, window, make_document(status=SourceStatus.INACTIVE))

    sweep(manifest, window, make_document(status=SourceStatus.CANCELLED))

    assert manifest.documents.require(document.identity).local_state is LocalState.CANCELLED


def test_an_unknown_inactive_row_is_neither_archived_nor_created(
    manifest: Manifest, window: RetentionWindow
) -> None:
    superseded = make_document(status=SourceStatus.INACTIVE)
    cancelled = make_document(document_id=160477, status=SourceStatus.CANCELLED)

    outcome = sweep(manifest, window, superseded, cancelled)

    assert outcome.unknown_inactive == 2
    assert manifest.documents.get(superseded.identity) is None
    assert manifest.documents.get(cancelled.identity) is None


def test_documents_outside_the_criteria_are_skipped_and_re_evaluated_every_run(
    manifest: Manifest, window: RetentionWindow
) -> None:
    document = make_document(category="ITR - Informacoes Trimestrais")

    def only_events(candidate: SourceDocument) -> bool:
        return candidate.category == "Fato Relevante"

    first = sweep(manifest, window, document, criteria=only_events)
    assert first.skipped == (document.identity,)
    assert manifest.documents.require(document.identity).local_state is LocalState.SKIPPED

    # The criteria widen; the same row, seen again, joins the queue without a new sighting.
    second = sweep(manifest, window, document)
    assert second.queued == (document.identity,)
    assert manifest.documents.require(document.identity).local_state is LocalState.DISCOVERED


def test_narrowed_criteria_take_a_document_out_of_the_queue_before_it_is_fetched(
    manifest: Manifest, window: RetentionWindow
) -> None:
    document = make_document()
    sweep(manifest, window, document)

    outcome = sweep(manifest, window, document, criteria=lambda candidate: False)

    assert outcome.skipped == (document.identity,)
    assert manifest.documents.require(document.identity).local_state is LocalState.SKIPPED


def test_a_document_the_source_re_activates_returns_to_the_queue(
    manifest: Manifest, window: RetentionWindow
) -> None:
    document = make_document()
    sweep(manifest, window, document)
    sweep(manifest, window, make_document(status=SourceStatus.INACTIVE))

    outcome = sweep(manifest, window, document)

    assert outcome.queued == (document.identity,)
    assert manifest.documents.require(document.identity).local_state is LocalState.DISCOVERED


def test_a_row_delivered_outside_the_window_is_refused_loudly(
    manifest: Manifest, window: RetentionWindow, caplog: pytest.LogCaptureFixture
) -> None:
    stray = make_document(delivery_date=date(2026, 1, 5))
    source = FakeSource(stray=[stray])

    with caplog.at_level(logging.WARNING):
        outcome = discover(source, manifest, window=window, watched=(PETR,))

    assert outcome.out_of_window == 1
    assert manifest.documents.get(stray.identity) is None
    assert "outside the queried window" in caplog.text


def test_an_empty_watch_list_makes_no_request(manifest: Manifest, window: RetentionWindow) -> None:
    source = FakeSource([make_document()])

    outcome = discover(source, manifest, window=window, watched=())

    assert source.requested == []
    assert outcome.observed == 0


def test_two_watched_companies_are_both_kept(
    manifest: Manifest, window: RetentionWindow
) -> None:
    petrobras = make_document()
    vale = make_document(document_id=160477, cvm_code="004170", legal_name="VALE S.A.")

    outcome = sweep(manifest, window, petrobras, vale, watched=(PETR, VALE))

    assert set(outcome.queued) == {petrobras.identity, vale.identity}


def test_the_watermark_records_the_last_completed_sweep(
    manifest: Manifest, window: RetentionWindow
) -> None:
    sweep(manifest, window, make_document())

    assert manifest.state.watermark() == window.last


def test_a_watermark_older_than_the_window_is_reported_as_unobserved_days(
    manifest: Manifest, window: RetentionWindow, caplog: pytest.LogCaptureFixture
) -> None:
    manifest.state.set_watermark(window.first - date.resolution * 3)

    with caplog.at_level(logging.WARNING):
        sweep(manifest, window, make_document())

    assert "never observed" in caplog.text
    assert manifest.state.watermark() == window.last
