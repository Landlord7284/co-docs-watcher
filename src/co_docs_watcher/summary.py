"""One run, consolidated into the few lines a person reads after it.

The log narrates a run while it happens: every step writes its own line, and the streams carry
those lines interleaved with whatever the source did to earn them. That is the right shape for
someone watching a run and the wrong one for the question asked once it is over — *what did
this run do?* — whose answer is otherwise spread across seven entries with however many
warnings in between.

So the report is rendered a second time, at the end, as a fixed table: one row per step, in
the order the steps ran, carrying the same counters every time. The shape does not vary with
the outcome. A row printed only when its counter is non-zero turns "nothing happened" into
"nothing was printed", and to someone scanning a run those two read alike — the same reason
the inbox names a document it could not fetch instead of leaving the day silent. Zeros are
printed, and a step the run never reached says so in its own words rather than by absence.

The table goes to stdout, like ``doctor``'s findings and ``status``'s report, and is not a log
record: the log already holds each step's own line, and a second copy of the same counters
under a timestamp would be one more pair of numbers to reconcile.
"""

from __future__ import annotations

from collections.abc import Callable

from co_docs_watcher.clock import RetentionWindow, directory_name
from co_docs_watcher.run import RunReport

__all__ = ["summary_lines"]


def _windows(report: RunReport) -> str:
    """Both windows, named, because they are configured apart and differ on purpose.

    A monitor run sweeps two days and purges seven, and a summary that printed one window
    would read as a run that had just deleted five days it never looked at.
    """
    return (
        f"discovery {_window(report.discovery_window)}, "
        f"retention {_window(report.retention_window)}"
    )


def _window(window: RetentionWindow) -> str:
    return f"{window.first} .. {window.last} ({window.days} dates)"


def _reconcile(report: RunReport) -> str:
    outcome = report.reconciled
    return (
        f"recovered={len(outcome.recovered)} requeued={len(outcome.requeued)} "
        f"failed={len(outcome.failed)} enacted={len(outcome.enacted)} "
        f"files_removed={outcome.removed_files} staging_discarded={outcome.discarded_staging}"
    )


def _registry(report: RunReport) -> str:
    """What the registry costs when it is missing, said in those terms.

    The run does not need it — the watch list carries every resolved prefix — so the honest
    report of a failed refresh is not that the run degraded but that ``add`` is refused until
    a package can be fetched again.
    """
    if report.registry_error is None:
        return "available"
    return f"unavailable: {report.registry_error} (`add` refuses until it can be fetched)"


def _discovery(report: RunReport) -> str:
    outcome = report.discovery
    if outcome is None:
        return "not reached"
    return (
        f"rows={outcome.observed} watched={outcome.watched} queued={len(outcome.queued)} "
        f"skipped={len(outcome.skipped)} unchanged={outcome.unchanged} "
        f"deactivated={len(outcome.deactivated)} cancelled={len(outcome.cancelled)} "
        f"refused={outcome.refused}"
    )


def _fetch(report: RunReport) -> str:
    outcome = report.fetch
    if outcome is None:
        return "not reached"
    return (
        f"available={len(outcome.available)} retrying={len(outcome.retrying)} "
        f"failed={len(outcome.failed)} bytes={outcome.archived_bytes:,}"
    )


def _purge(report: RunReport) -> str:
    outcome = report.purged
    return (
        f"documents={len(outcome.purged)} dates={len(outcome.removed_dates)} "
        f"indexes={len(outcome.removed_indexes)} unremoved={len(outcome.unremoved_dates)}"
    )


def _inbox(report: RunReport) -> str:
    outcome = report.inbox
    return (
        f"written={len(outcome.written)} unchanged={len(outcome.unchanged)} "
        f"removed={len(outcome.removed)} entries={outcome.entries} "
        f"refused={len(outcome.refused)} today={_today_index(report)}"
    )


def _today_index(report: RunReport) -> str:
    """Where today's reading queue is, or that there is none.

    Named relative to ``documents_root``, because what a reader opens is the file and not the
    root it happens to be installed under. ``none`` is an answer of its own: today's index
    exists only when today has something to report, and printing the path of a file this run
    did not write is the one way this row could mislead.
    """
    today = report.retention_window.last
    if today in report.inbox.written or today in report.inbox.unchanged:
        return f"_inbox/{directory_name(today)}.md"
    return "none"


def _result(report: RunReport) -> str:
    """The verdict, and the exit code it produced, spelled out beside each other.

    A run cut short by the source is reported as such even though the exit code it earns is
    the same ``1`` an isolated failure earns: the counters above it are then a partial count,
    and a summary that let them read as a complete one would be the silent divergence this
    whole table exists to prevent.
    """
    verdict = "clean" if report.clean else "isolated failures"
    line = f"{verdict} (exit {report.exit_code.value})"
    if report.interrupted is not None:
        line += f"; the counters above are partial — the run was cut short: {report.interrupted}"
    return line


#: Every row of the table, in the order the steps ran. One list, so that a row cannot be
#: renamed without its renderer travelling with it.
_ROWS: tuple[tuple[str, Callable[[RunReport], str]], ...] = (
    ("windows", _windows),
    ("reconcile", _reconcile),
    ("registry", _registry),
    ("discovery", _discovery),
    ("fetch", _fetch),
    ("purge", _purge),
    ("inbox", _inbox),
    ("result", _result),
)

#: The column every row's text starts at, sized from the whole set of labels rather than from
#: the rows of one run: two runs of the same archive have to line up under each other.
_COLUMN = max(len(label) for label, _ in _ROWS) + 2


def summary_lines(report: RunReport) -> tuple[str, ...]:
    """The run's report as one aligned row per step, ready to print."""
    return tuple(f"{label:<{_COLUMN}}{render(report)}" for label, render in _ROWS)
