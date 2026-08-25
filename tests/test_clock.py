"""Window arithmetic, directory names, and the refusal to read the host's clock."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from co_docs_watcher import clock as clock_module
from co_docs_watcher.clock import (
    Clock,
    RetentionWindow,
    directory_name,
    parse_directory_name,
    source_timezone,
    window_ending,
)
from co_docs_watcher.config import load_config
from co_docs_watcher.errors import ConfigError

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(autouse=True)
def _uninstalled_timezone() -> None:
    clock_module._source_timezone = None


def test_a_window_of_one_is_today_alone() -> None:
    window = window_ending(date(2026, 8, 24), 1)
    assert window.first == window.last == date(2026, 8, 24)
    assert window.dates == [date(2026, 8, 24)]


def test_n_counts_retained_dates_including_today() -> None:
    window = window_ending(date(2026, 8, 24), 7)
    assert window.first == date(2026, 8, 18)
    assert window.days == 7
    assert len(window.dates) == 7


@pytest.mark.parametrize(
    ("last", "days", "first"),
    [
        (date(2026, 3, 2), 5, date(2026, 2, 26)),  # across a month boundary
        (date(2026, 1, 3), 5, date(2025, 12, 30)),  # across a year boundary
        (date(2028, 3, 1), 2, date(2028, 2, 29)),  # across a leap day
    ],
)
def test_window_arithmetic_across_boundaries(last: date, days: int, first: date) -> None:
    window = window_ending(last, days)
    assert window.first == first
    assert window.dates[0] == first
    assert window.dates[-1] == last


def test_the_sweep_order_is_most_recent_first() -> None:
    """What a manual run feels: today's publications are asked for before last week's."""
    window = window_ending(date(2026, 8, 24), 7)

    assert window.dates_newest_first[0] == date(2026, 8, 24)
    assert window.dates_newest_first[-1] == window.first
    # The same days, in the other order: an order is a policy, not a different window.
    assert sorted(window.dates_newest_first) == window.dates


def test_a_window_must_retain_something() -> None:
    with pytest.raises(ValueError, match="at least one date"):
        window_ending(date(2026, 8, 24), 0)
    with pytest.raises(ValueError, match="empty window"):
        RetentionWindow(first=date(2026, 8, 24), last=date(2026, 8, 23))


def test_one_frontier_serves_discovery_purge_and_inbox() -> None:
    window = window_ending(date(2026, 8, 24), 7)
    frontier = window.first

    # Discovery sweeps the window, purge deletes what is before it, the inbox rewrites it:
    # all three read the same boundary, so purge never deletes what discovery re-downloads.
    assert min(window.dates) == frontier
    assert window.is_expired(frontier - timedelta(days=1))
    assert not window.is_expired(frontier)
    assert all(window.contains(day) for day in window.dates)


def test_directory_names_sort_chronologically() -> None:
    window = window_ending(date(2026, 1, 3), 10)
    names = [directory_name(day) for day in window.dates]
    assert names[0] == "2025-12-25"
    assert sorted(names) == names
    assert [parse_directory_name(name) for name in names] == window.dates


def test_directory_names_are_zero_padded() -> None:
    assert directory_name(date(2026, 1, 3)) == "2026-01-03"
    assert len(directory_name(date(2026, 1, 3))) == 10


def test_the_timezone_must_be_installed_before_it_is_read() -> None:
    with pytest.raises(ConfigError, match="not installed"):
        source_timezone()


def test_config_load_installs_the_source_timezone(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        '[paths]\ndata_root = "/a"\ndocuments_root = "/b"\nlogs_root = "/c"\n'
        '[source]\ntimezone = "America/Sao_Paulo"\n',
        encoding="utf-8",
    )
    load_config(env={}, cwd=tmp_path, home=tmp_path)
    assert source_timezone().key == "America/Sao_Paulo"
    assert Clock.installed().timezone.key == "America/Sao_Paulo"


def frozen(moment: datetime) -> type[datetime]:
    """A ``datetime`` whose ``now`` is fixed, so "today" can be asserted without waiting."""

    class Frozen(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            return moment if tz is None else moment.astimezone(tz)  # type: ignore[arg-type]

    return Frozen


def test_today_follows_the_source_not_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # 23:30 in Sao Paulo on the 24th is already the 25th in UTC — and the archive says the 24th.
    late_night = datetime(2026, 8, 24, 23, 30, tzinfo=SAO_PAULO)
    monkeypatch.setattr(clock_module, "datetime", frozen(late_night))

    for host_zone in ("UTC", "Asia/Tokyo", "America/Los_Angeles"):
        monkeypatch.setenv("TZ", host_zone)
        time.tzset()
        assert Clock(SAO_PAULO).today() == date(2026, 8, 24)
        assert Clock(SAO_PAULO).window(3).first == date(2026, 8, 22)
