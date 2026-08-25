"""Logging, stamped in the source's timezone.

The stdlib formatter renders timestamps through libc's localtime, which is the host's zone. On
a UTC container that stamps an event 21:40 while the same event is archived under the next
day's folder — the log and the archive then disagree about when something happened, and the
disagreement only shows up when someone is trying to explain an incident.

Installed once, at config load, alongside the timezone itself.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import IO
from zoneinfo import ZoneInfo

from co_docs_watcher.clock import source_timezone

__all__ = ["SourceTimeFormatter", "configure_logging"]

DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"

#: Handlers this module installed, so that reconfiguring never stacks duplicates.
_MARKER = "co_docs_watcher"


class SourceTimeFormatter(logging.Formatter):
    """A formatter whose ``asctime`` is read in the source's timezone."""

    def __init__(
        self,
        fmt: str = DEFAULT_FORMAT,
        datefmt: str | None = None,
        *,
        timezone: ZoneInfo,
    ) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.timezone = timezone

    # Overrides the stdlib's camelCase hook; the name is not ours to choose.
    def formatTime(
        self, record: logging.LogRecord, datefmt: str | None = None
    ) -> str:
        moment = datetime.fromtimestamp(record.created, self.timezone)
        if datefmt:
            return moment.strftime(datefmt)
        return moment.isoformat(sep=" ", timespec="milliseconds")


class _MaxLevelFilter(logging.Filter):
    """Keeps ordinary progress out of stderr, where operators look for problems."""

    def __init__(self, maximum: int) -> None:
        super().__init__()
        self.maximum = maximum

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.maximum


def configure_logging(
    *,
    timezone: ZoneInfo | None = None,
    level: int = logging.INFO,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> None:
    """Install source-anchored logging on the root logger.

    Progress goes to stdout, ``WARNING`` and above to stderr — no files, no rotation, no
    syslog: the process writes to its streams and whatever supervises it decides the rest.
    Calling this again replaces the handlers instead of stacking them.
    """
    timezone = source_timezone() if timezone is None else timezone
    formatter = SourceTimeFormatter(timezone=timezone)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_co_docs_watcher", None) == _MARKER:
            root.removeHandler(handler)
            handler.close()

    out = logging.StreamHandler(sys.stdout if stdout is None else stdout)
    out.setLevel(level)
    out.addFilter(_MaxLevelFilter(logging.INFO))
    err = logging.StreamHandler(sys.stderr if stderr is None else stderr)
    err.setLevel(logging.WARNING)

    for handler in (out, err):
        handler.setFormatter(formatter)
        handler._co_docs_watcher = _MARKER  # type: ignore[attr-defined]
        root.addHandler(handler)

    root.setLevel(min(level, logging.WARNING))
