"""Logging, stamped in the source's timezone and kept on disk.

The stdlib formatter renders timestamps through libc's localtime, which is the host's zone. On
a UTC container that stamps an event 21:40 while the same event is archived under the next
day's folder — the log and the archive then disagree about when something happened, and the
disagreement only shows up when someone is trying to explain an incident.

Progress goes to stdout and everything from ``WARNING`` up to stderr, and the same lines are
written to one rotating file under ``logs_root``. The streams are what a supervisor reads; the
file is what answers a question asked later, and a run whose one warning scrolled past is
exactly when that question is asked. The file never carries less than the streams, so the two
never have to be reconciled.

A file that cannot be opened does not stop the watcher: the failure is reported on stderr and
the run continues on the streams alone. Losing the copy is not worth losing the run.

Installed once, at config load, alongside the timezone itself.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO
from zoneinfo import ZoneInfo

from co_docs_watcher.clock import source_timezone

__all__ = ["DEFAULT_BACKUPS", "DEFAULT_MAX_BYTES", "SourceTimeFormatter", "configure_logging"]

DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"

#: Rotation defaults, mirrored from the configuration so that a caller passing no file
#: policy still gets a bounded one.
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUPS = 5

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
    log_path: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backups: int = DEFAULT_BACKUPS,
) -> None:
    """Install source-anchored logging on the root logger.

    Progress goes to stdout and ``WARNING`` and above to stderr; ``log_path``, when given,
    also receives everything from ``level`` up, rotating at ``max_bytes`` and keeping
    ``backups`` older files. Omitting it leaves the streams as the only destination — which
    is what the tests do, and what a caller with nowhere to write gets.

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

    handlers: list[logging.Handler] = [out, err]
    to_file = _file_handler(log_path, level=level, max_bytes=max_bytes, backups=backups)
    if to_file is not None:
        handlers.append(to_file)

    for handler in handlers:
        handler.setFormatter(formatter)
        handler._co_docs_watcher = _MARKER  # type: ignore[attr-defined]
        root.addHandler(handler)

    root.setLevel(min(level, logging.WARNING))


def _file_handler(
    log_path: Path | None, *, level: int, max_bytes: int, backups: int
) -> logging.Handler | None:
    """Open the rotating log file, creating its directory. ``None`` when there is no file.

    The directory is created here rather than demanded of the operator, for the same reason
    the archive creates its own date folders: a root named in the configuration is a
    statement of where things go, not a promise that someone already made it.
    """
    if log_path is None:
        return None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
    except OSError as error:
        # Before any handler is installed, so this cannot be logged — and it must still be
        # seen. The run goes on: the streams carry everything the file would have.
        print(f"warning: cannot write the log file {log_path}: {error}", file=sys.stderr)
        return None
    handler.setLevel(level)
    return handler
