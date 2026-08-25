"""Log timestamps follow the source, and progress and problems go to different streams."""

from __future__ import annotations

import contextlib
import io
import logging
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from co_docs_watcher import clock as clock_module
from co_docs_watcher.clock import install_source_timezone
from co_docs_watcher.logging_setup import SourceTimeFormatter, configure_logging

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
TOKYO = ZoneInfo("Asia/Tokyo")

# 2026-08-24 23:30:00 in Sao Paulo (UTC-3) — already the 25th in UTC and in Tokyo.
LATE_NIGHT = 1787625000.0


@pytest.fixture(autouse=True)
def _clean_logging() -> None:
    clock_module._source_timezone = None
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


def record_at(created: float) -> logging.LogRecord:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "published", None, None)
    record.created = created
    return record


def test_timestamps_are_stamped_in_the_source_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    formatter = SourceTimeFormatter(timezone=SAO_PAULO)
    for host_zone in ("UTC", "Asia/Tokyo", "America/Los_Angeles"):
        monkeypatch.setenv("TZ", host_zone)
        time.tzset()
        stamped = formatter.formatTime(record_at(LATE_NIGHT))
        # The host would call this the 25th; the archive files it under the 24th.
        assert stamped.startswith("2026-08-24 23:30:00")


def test_a_different_source_timezone_stamps_differently() -> None:
    assert SourceTimeFormatter(timezone=TOKYO).formatTime(record_at(LATE_NIGHT)).startswith(
        "2026-08-25 11:30:00"
    )


def test_an_explicit_datefmt_is_honoured() -> None:
    formatter = SourceTimeFormatter(timezone=SAO_PAULO)
    assert formatter.formatTime(record_at(LATE_NIGHT), "%Y%m%d") == "20260824"


def test_configure_logging_reads_the_installed_timezone() -> None:
    install_source_timezone(SAO_PAULO)
    out, err = io.StringIO(), io.StringIO()
    configure_logging(stdout=out, stderr=err)

    logger = logging.getLogger("co_docs_watcher.test")
    logger.info("swept 7 days")
    logger.warning("source answered temErro")

    assert "swept 7 days" in out.getvalue()
    assert "swept 7 days" not in err.getvalue()
    assert "temErro" in err.getvalue()
    # Progress never duplicated across the two streams.
    assert out.getvalue().count("swept 7 days") == 1


def test_reconfiguring_replaces_handlers_instead_of_stacking_them() -> None:
    install_source_timezone(SAO_PAULO)
    out = io.StringIO()
    for _ in range(3):
        configure_logging(stdout=out, stderr=io.StringIO())

    logging.getLogger("co_docs_watcher.test").info("once")
    assert out.getvalue().count("once") == 1


def test_debug_is_silent_unless_asked(caplog: pytest.LogCaptureFixture) -> None:
    install_source_timezone(SAO_PAULO)
    out = io.StringIO()
    configure_logging(stdout=out, stderr=io.StringIO())
    logging.getLogger("co_docs_watcher.test").debug("payload dump")
    assert out.getvalue() == ""

    configure_logging(stdout=out, stderr=io.StringIO(), level=logging.DEBUG)
    logging.getLogger("co_docs_watcher.test").debug("payload dump")
    assert "payload dump" in out.getvalue()


# --- The log file. ---


def test_the_log_file_receives_progress_and_problems_alike(tmp_path: Path) -> None:
    install_source_timezone(SAO_PAULO)
    log_path = tmp_path / "logs" / "co-docs-watcher.log"
    configure_logging(stdout=io.StringIO(), stderr=io.StringIO(), log_path=log_path)

    logger = logging.getLogger("co_docs_watcher.test")
    logger.info("swept 7 days")
    logger.warning("source answered temErro")

    written = log_path.read_text(encoding="utf-8")
    # The two streams split by severity; the file is whole, so it can be read on its own.
    assert "swept 7 days" in written
    assert "temErro" in written


def test_the_log_directory_is_created_rather_than_demanded(tmp_path: Path) -> None:
    install_source_timezone(SAO_PAULO)
    log_path = tmp_path / "absent" / "deeper" / "co-docs-watcher.log"
    configure_logging(stdout=io.StringIO(), stderr=io.StringIO(), log_path=log_path)

    logging.getLogger("co_docs_watcher.test").info("first line")
    assert log_path.exists()


def test_the_log_file_rotates_instead_of_growing_without_end(tmp_path: Path) -> None:
    install_source_timezone(SAO_PAULO)
    log_path = tmp_path / "logs" / "co-docs-watcher.log"
    configure_logging(
        stdout=io.StringIO(), stderr=io.StringIO(), log_path=log_path, max_bytes=512, backups=2
    )

    logger = logging.getLogger("co_docs_watcher.test")
    for index in range(200):
        logger.info("a line long enough to push the file past its cap: %d", index)

    kept = sorted(path.name for path in log_path.parent.iterdir())
    assert kept == ["co-docs-watcher.log", "co-docs-watcher.log.1", "co-docs-watcher.log.2"]
    assert all(path.stat().st_size < 2048 for path in log_path.parent.iterdir())


def test_a_log_file_that_cannot_be_opened_never_stops_the_run(tmp_path: Path) -> None:
    install_source_timezone(SAO_PAULO)
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()

    with contextlib.redirect_stderr(err):
        configure_logging(stdout=out, stderr=err, log_path=blocked / "co-docs-watcher.log")

    logging.getLogger("co_docs_watcher.test").info("the run goes on")
    assert "the run goes on" in out.getvalue()
    assert "cannot write the log file" in err.getvalue()
