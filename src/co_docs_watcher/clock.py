"""Time, anchored on the source.

Every date this system reasons about — "today", the retention frontier, directory names, the
inbox, the watermark — is read in the source's timezone, never the host's. A container in UTC
would otherwise archive a document delivered at 22:00 in São Paulo under the following day, and
the archive would disagree with the regulator about when things were published.

The window is a single object, computed once and shared: discovery, purge and inbox all read
the same frontier. Computing it twice is how a purge deletes what discovery will download again
on the next run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from co_docs_watcher.errors import ConfigError

__all__ = [
    "Clock",
    "RetentionWindow",
    "directory_name",
    "install_source_timezone",
    "parse_directory_name",
    "source_timezone",
    "window_ending",
]

_source_timezone: ZoneInfo | None = None


def install_source_timezone(timezone: ZoneInfo) -> None:
    """Install the process-wide source timezone. Called once, at config load."""
    global _source_timezone
    _source_timezone = timezone


def source_timezone() -> ZoneInfo:
    """The installed source timezone.

    Raises rather than defaulting to the host: a plausible wrong answer here is invisible and
    corrupts dates everywhere downstream.
    """
    if _source_timezone is None:
        raise ConfigError("source timezone is not installed; load the configuration first")
    return _source_timezone


@dataclass(frozen=True, slots=True)
class RetentionWindow:
    """The closed interval ``[first, last]`` of retained delivery dates.

    ``N`` counts retained dates **including today**, so a window of one day is today alone.
    """

    first: date
    last: date

    def __post_init__(self) -> None:
        if self.first > self.last:
            raise ValueError(f"empty window: {self.first} > {self.last}")

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1

    @property
    def dates(self) -> list[date]:
        """Every date in the window, oldest first — the window's contents, as a list."""
        return [self.first + timedelta(days=offset) for offset in range(self.days)]

    @property
    def dates_newest_first(self) -> list[date]:
        """The same dates, most recent first — the order the sweep asks for them in.

        Order is a policy of the sweep and not a property of the window, which is why it is
        a second name rather than a reversal of the first: the inbox and the retention
        frontier read ``dates`` as a set of days, and would be indifferent to it.
        """
        return [self.last - timedelta(days=offset) for offset in range(self.days)]

    def contains(self, day: date) -> bool:
        return self.first <= day <= self.last

    def is_expired(self, day: date) -> bool:
        """Whether a delivery date has aged out. The purge frontier, and only this one."""
        return day < self.first


@dataclass(frozen=True, slots=True)
class Clock:
    """Reads the wall clock in the source's timezone."""

    timezone: ZoneInfo

    @classmethod
    def installed(cls) -> Clock:
        return cls(source_timezone())

    def now(self) -> datetime:
        return datetime.now(UTC).astimezone(self.timezone)

    def today(self) -> date:
        return self.now().date()

    def window(self, retention_days: int) -> RetentionWindow:
        """The retention window ending today. The single frontier every step reads."""
        return window_ending(self.today(), retention_days)


def window_ending(last: date, retention_days: int) -> RetentionWindow:
    """``[last - (N - 1), last]``. Pure, so the arithmetic can be tested without a clock."""
    if retention_days < 1:
        raise ValueError(f"retention must keep at least one date, got {retention_days}")
    return RetentionWindow(first=last - timedelta(days=retention_days - 1), last=last)


def directory_name(day: date) -> str:
    """``yyyy-mm-dd``, zero-padded — so that lexicographic order equals chronological order."""
    return day.isoformat()


def parse_directory_name(name: str) -> date:
    """Inverse of :func:`directory_name`. Rejects anything that is not a date directory."""
    return date.fromisoformat(name)
