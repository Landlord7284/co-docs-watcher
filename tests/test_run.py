"""Orchestration of one run: step order, graded failure, and the two aborts with own codes.

The source here is the in-memory fake — what the adapter produces is pinned by the contract
tests, and the full flow over the wire belongs to the integration suite. These tests are about
the decisions ``run.py`` owns: which failures end the run, which are carried, and what still
happens after the source refuses to keep answering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from co_docs_watcher.clock import RetentionWindow, window_ending
from co_docs_watcher.config import DEFAULT_LOG_BACKUPS, DEFAULT_LOG_MAX_BYTES, Config
from co_docs_watcher.errors import (
    CaptchaRequiredError,
    DocumentError,
    LockHeldError,
    RequestBudgetExceededError,
    TransientSourceError,
)
from co_docs_watcher.lock import RunLock
from co_docs_watcher.manifest.db import open_manifest
from co_docs_watcher.manifest.repo import Manifest
from co_docs_watcher.models import LocalState, SourceStatus
from co_docs_watcher.run import execute_run
from tests.conftest import CLOCK, TODAY
from tests.fca import build_package
from tests.pipeline import FakeSource
from tests.test_models import make_document

TIMEZONE = ZoneInfo("America/Sao_Paulo")

WATCH_LIST = """\
companies:
  - cvm_code: '009512'
    prefix: PETR
    prefix_source: ticker
    matched_by: ticker
    legal_name: PETROLEO BRASILEIRO S.A. PETROBRAS
"""


@dataclass(frozen=True, slots=True)
class FixedClock:
    """A clock pinned to :data:`TODAY`, so the window under test never moves."""

    timezone: ZoneInfo = TIMEZONE

    def now(self) -> datetime:
        return datetime(2026, 8, 24, 12, 0, tzinfo=self.timezone)

    def today(self) -> date:
        return TODAY

    def window(self, retention_days: int) -> RetentionWindow:
        return window_ending(TODAY, retention_days)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    data_root = tmp_path / "data"
    cache = data_root / "cvm-cache"
    cache.mkdir(parents=True)
    for year in (2025, 2026):
        (cache / f"fca_cia_aberta_{year}.zip").write_bytes(build_package(year=year))
    (data_root / "companies.yaml").write_text(WATCH_LIST, encoding="utf-8")
    return Config(
        data_root=data_root,
        documents_root=tmp_path / "documents",
        logs_root=tmp_path / "logs",
        log_max_bytes=DEFAULT_LOG_MAX_BYTES,
        log_backups=DEFAULT_LOG_BACKUPS,
        timezone=TIMEZONE,
        retention_days=7,
        discovery_days=7,
        monitor_days=2,
        min_request_interval=0.01,
        max_requests_per_run=200,
        registry_max_age_days=7,
        source_base_url="http://localhost:9/",
        prefix_overrides={},
        origin=tmp_path / "config.toml",
    )


def run(config: Config, source: FakeSource, **kwargs: object):
    return execute_run(config, source=source, clock=FixedClock(), **kwargs)  # type: ignore[arg-type]


def manifest_of(config: Config) -> Manifest:
    return Manifest.over(open_manifest(config.manifest_path), CLOCK)


def test_a_clean_run_archives_indexes_and_reports_clean(config: Config) -> None:
    document = make_document(delivery_date=TODAY)
    report = run(config, FakeSource([document]))

    assert report.clean
    assert int(report.exit_code) == 0
    archived = config.documents_root / TODAY.isoformat() / "PETR"
    assert (archived / "Fato-Relevante_160310_V01.pdf").is_file()
    assert (config.inbox_root / f"{TODAY.isoformat()}.md").is_file()

    manifest = manifest_of(config)
    assert manifest.documents.require(document.identity).local_state is LocalState.AVAILABLE
    assert manifest.state.watermark() == TODAY


def test_the_sweep_covers_the_whole_window_never_an_increment(config: Config) -> None:
    source = FakeSource([])
    run(config, source)
    run(config, source)
    expected = [TODAY - timedelta(days=offset) for offset in range(7)]
    assert source.requested == [expected, expected]


def test_a_second_run_downloads_nothing_and_leaves_the_inbox_alone(config: Config) -> None:
    document = make_document(delivery_date=TODAY)
    source = FakeSource([document])
    run(config, source)
    index = (config.inbox_root / f"{TODAY.isoformat()}.md").read_text(encoding="utf-8")

    report = run(config, source)
    assert report.clean
    assert source.downloaded == [document.identity]
    assert (config.inbox_root / f"{TODAY.isoformat()}.md").read_text(encoding="utf-8") == index


def test_the_steps_run_in_the_documented_order(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        run(config, FakeSource([make_document(delivery_date=TODAY)]))
    messages = [record.getMessage() for record in caplog.records]
    order = [
        next(index for index, message in enumerate(messages) if message.startswith(marker))
        for marker in ("discovery:", "fetch:", "inbox:")
    ]
    assert order == sorted(order)


def test_a_registry_that_cannot_be_read_downgrades_to_a_warning(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    for year in (2025, 2026):
        (config.registry_cache_root / f"fca_cia_aberta_{year}.zip").write_bytes(b"not a zip")

    document = make_document(delivery_date=TODAY)
    with caplog.at_level(logging.WARNING):
        report = run(config, FakeSource([document]))

    # Monitoring happened anyway: the watch list carries everything a run needs.
    assert manifest_of(config).documents.require(document.identity).local_state is (
        LocalState.AVAILABLE
    )
    assert report.registry_error is not None
    assert not report.clean and int(report.exit_code) == 1
    assert any("registry" in record.getMessage() for record in caplog.records)


def test_an_isolated_document_failure_never_kills_the_batch(config: Config) -> None:
    wounded = make_document(document_id=1, delivery_date=TODAY)
    healthy = make_document(document_id=2, delivery_date=TODAY)
    source = FakeSource(
        [wounded, healthy], failures={wounded.identity: [DocumentError("mangled")]}
    )

    report = run(config, source)
    assert not report.clean and int(report.exit_code) == 1
    assert report.fetch is not None
    assert report.fetch.retrying == (wounded.identity,)
    assert report.fetch.available == (healthy.identity,)


def test_a_captcha_during_fetch_aborts_with_the_queue_put_back(config: Config) -> None:
    document = make_document(delivery_date=TODAY)
    source = FakeSource([document], failures={document.identity: [CaptchaRequiredError("S")]})

    with pytest.raises(CaptchaRequiredError):
        run(config, source)

    # Nothing corrupted: the document is back in the queue and the lock is free again.
    assert manifest_of(config).documents.require(document.identity).local_state is (
        LocalState.DISCOVERED
    )
    with RunLock(config.lock_path):
        pass


def test_a_captcha_during_the_sweep_propagates_untouched(config: Config) -> None:
    class CaptchaSource(FakeSource):
        def list_window(self, days):  # type: ignore[override]
            raise CaptchaRequiredError("S")

    with pytest.raises(CaptchaRequiredError):
        run(config, CaptchaSource())


def test_a_held_lock_refuses_the_run_before_it_touches_anything(config: Config) -> None:
    with RunLock(config.lock_path), pytest.raises(LockHeldError):
        run(config, FakeSource([make_document(delivery_date=TODAY)]))
    assert not config.manifest_path.exists()


def test_a_burned_out_budget_still_purges_and_reindexes(config: Config) -> None:
    document = make_document(delivery_date=TODAY)
    source = FakeSource(
        [document], failures={document.identity: [RequestBudgetExceededError("fuse")]}
    )
    stale_day = TODAY - timedelta(days=30)
    stale_index = config.inbox_root / f"{stale_day.isoformat()}.md"
    stale_index.parent.mkdir(parents=True)
    stale_index.write_text("stale\n", encoding="utf-8")

    report = run(config, source)

    assert report.interrupted is not None
    assert not report.clean and int(report.exit_code) == 1
    assert not stale_index.exists()
    assert manifest_of(config).documents.require(document.identity).local_state is (
        LocalState.DISCOVERED
    )


def test_a_backend_that_stays_down_ends_the_run_with_the_batch_intact(config: Config) -> None:
    class DownSource(FakeSource):
        def list_window(self, days):  # type: ignore[override]
            raise TransientSourceError("temErro")

    report = run(config, DownSource())
    assert report.discovery is None and report.fetch is None
    assert report.interrupted is not None
    assert not report.clean and int(report.exit_code) == 1


def test_a_cancellation_observed_by_the_sweep_is_enacted_in_the_same_run(
    config: Config,
) -> None:
    document = make_document(delivery_date=TODAY)
    source = FakeSource([document])
    run(config, source)
    archived = (
        config.documents_root / TODAY.isoformat() / "PETR" / "Fato-Relevante_160310_V01.pdf"
    )
    assert archived.is_file()

    source.documents = [make_document(delivery_date=TODAY, status=SourceStatus.CANCELLED)]
    report = run(config, source)

    assert report.clean
    assert not archived.exists()
    assert manifest_of(config).documents.require(document.identity).local_state is (
        LocalState.CANCELLED
    )
    index = (config.inbox_root / f"{TODAY.isoformat()}.md").read_text(encoding="utf-8")
    assert "cancelled at the source" in index
