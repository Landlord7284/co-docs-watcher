"""The consolidated table a run prints when it is over.

What is pinned here is the shape, not the prose: one row per step in the order the steps ran,
every counter present whether or not it moved, and a step the run never reached saying so
rather than reporting zeros it never measured.
"""

from __future__ import annotations

import re
from datetime import timedelta

from co_docs_watcher.clock import window_ending
from co_docs_watcher.pipeline import (
    DiscoveryOutcome,
    FetchOutcome,
    InboxOutcome,
    PurgeOutcome,
    ReconcileOutcome,
)
from co_docs_watcher.run import RunReport
from co_docs_watcher.summary import summary_lines
from tests.conftest import TODAY

STEPS = ["windows", "reconcile", "registry", "discovery", "fetch", "purge", "inbox", "result"]

FETCHED = (160310, 1)

#: A quiet everything, so that a test narrows exactly the step it is about.
NOTHING_RECONCILED = ReconcileOutcome((), (), (), (), 0, 0)
NOTHING_PURGED = PurgeOutcome((), (), (), ())


def make_report(**overrides: object) -> RunReport:
    """A clean monitor run that archived one document — what every test starts from."""
    defaults: dict[str, object] = {
        "retention_window": window_ending(TODAY, 7),
        "discovery_window": window_ending(TODAY, 2),
        "reconciled": NOTHING_RECONCILED,
        "registry_error": None,
        "discovery": DiscoveryOutcome(
            observed=902,
            ignored=895,
            out_of_window=0,
            unknown_inactive=0,
            unchanged=6,
            refused=0,
            queued=(FETCHED,),
            skipped=(),
            deactivated=(),
            cancelled=(),
        ),
        "fetch": FetchOutcome((FETCHED,), (), (), 2_307_427),
        "purged": NOTHING_PURGED,
        "inbox": InboxOutcome((TODAY,), (), (), 1, ()),
        "interrupted": None,
    }
    return RunReport(**(defaults | overrides))  # type: ignore[arg-type]


def rows(report: RunReport) -> dict[str, str]:
    """The table read back as ``label -> text``, which is how the assertions read it."""
    return {label: text for label, _, text in map(_cell, summary_lines(report))}


def _cell(line: str) -> tuple[str, int, str]:
    match = re.fullmatch(r"(\S+)( +)(.*)", line)
    assert match is not None, f"not a row of the table: {line!r}"
    label, gap, text = match.groups()
    return label, len(label) + len(gap), text


def test_every_step_gets_a_row_in_the_order_it_ran() -> None:
    assert [label for label, _, _ in map(_cell, summary_lines(make_report()))] == STEPS


def test_the_rows_start_in_one_column() -> None:
    """Sized from every label there is, so two runs of one archive line up under each other."""
    starts = {start for _, start, _ in map(_cell, summary_lines(make_report()))}
    assert len(starts) == 1
    assert starts.pop() > max(len(label) for label in STEPS)


def test_both_windows_are_named_because_they_differ_on_purpose() -> None:
    """A monitor run sweeps two days and purges seven; one window would read as a bug."""
    week, pair = window_ending(TODAY, 7), window_ending(TODAY, 2)
    assert rows(make_report())["windows"] == (
        f"discovery {pair.first} .. {pair.last} (2 dates), "
        f"retention {week.first} .. {week.last} (7 dates)"
    )


def test_a_quiet_run_prints_every_counter_rather_than_going_silent() -> None:
    """"Nothing happened" and "nothing was printed" must not read the same way."""
    quiet = rows(
        make_report(
            discovery=DiscoveryOutcome(0, 0, 0, 0, 0, 0, (), (), (), ()),
            fetch=FetchOutcome((), (), (), 0),
            inbox=InboxOutcome((), (), (), 0, ()),
        )
    )
    assert quiet["discovery"] == (
        "rows=0 watched=0 queued=0 skipped=0 unchanged=0 deactivated=0 cancelled=0 refused=0"
    )
    assert quiet["fetch"] == "available=0 retrying=0 failed=0 bytes=0"
    assert quiet["purge"] == "documents=0 dates=0 indexes=0 unremoved=0"
    assert quiet["reconcile"] == (
        "recovered=0 requeued=0 failed=0 enacted=0 files_removed=0 staging_discarded=0"
    )


def test_the_rows_that_moved_carry_what_the_run_did() -> None:
    reported = rows(make_report())
    assert reported["discovery"] == (
        "rows=902 watched=7 queued=1 skipped=0 unchanged=6 deactivated=0 cancelled=0 refused=0"
    )
    assert reported["fetch"] == "available=1 retrying=0 failed=0 bytes=2,307,427"
    assert reported["inbox"].startswith("written=1 unchanged=0 removed=0 entries=1 refused=0")


def test_a_step_the_run_never_reached_says_so_instead_of_reporting_zeros() -> None:
    """Zeros would be a measurement; the sweep never happened, and the table says that."""
    cut_short = rows(
        make_report(
            discovery=None,
            fetch=None,
            interrupted="the request budget of 200 request(s) was spent",
        )
    )
    assert cut_short["discovery"] == "not reached"
    assert cut_short["fetch"] == "not reached"


def test_a_run_cut_short_says_its_counters_are_partial() -> None:
    result = rows(make_report(interrupted="the request budget was spent"))["result"]
    assert result.startswith("isolated failures (exit 1)")
    assert "partial" in result
    assert "the request budget was spent" in result


def test_the_verdict_carries_the_exit_code_the_process_returns() -> None:
    assert rows(make_report())["result"] == "clean (exit 0)"
    failed = make_report(fetch=FetchOutcome((), (), (FETCHED,), 0))
    assert rows(failed)["result"] == "isolated failures (exit 1)"


def test_a_registry_that_could_not_be_refreshed_reports_what_it_costs() -> None:
    """Not the run — the watch list carries every prefix. What it costs is ``add``."""
    registry = rows(make_report(registry_error="no usable package for 2026"))["registry"]
    assert registry.startswith("unavailable: no usable package for 2026")
    assert "`add`" in registry
    assert rows(make_report())["registry"] == "available"


def test_todays_index_is_named_where_a_reader_opens_it() -> None:
    """Relative to ``documents_root``: the file is what is read, not the root under it."""
    assert rows(make_report())["inbox"].endswith(f"today=_inbox/{TODAY.isoformat()}.md")


def test_an_index_that_was_already_right_is_still_todays_index() -> None:
    report = make_report(inbox=InboxOutcome((), (TODAY,), (), 1, ()))
    assert rows(report)["inbox"].endswith(f"today=_inbox/{TODAY.isoformat()}.md")


def test_a_day_with_nothing_to_report_has_no_index_and_the_row_says_none() -> None:
    """The path of a file this run did not write is the one way this row could mislead."""
    report = make_report(inbox=InboxOutcome((TODAY - timedelta(days=1),), (), (), 1, ()))
    assert rows(report)["inbox"].endswith("today=none")
