"""Discovery: merge the global sweep into the manifest.

The source has no per-company query. One request per day of the window returns the whole
market — some 450 documents on a normal day — and the watch list is applied *here*, against
field 0 of every row. That is why a watch list of one company and a watch list of eighty cost
exactly the same number of requests.

Three things this step deliberately does not do:

*It does not download.* A rediscovered document updates its mutable fields and stays where it
is; only a document seen for the first time, or one whose criteria changed, joins the queue.
The source returns the whole window on every run, so anything else would re-download the
archive daily.

*It does not archive what is not ``ACTIVE``.* The listing returns all three statuses, because
status is not a server-side filter. ``INACTIVE`` and ``CANCELLED`` rows are read only to
reconcile documents the manifest already knows: an inactive row for an unknown document is a
supersession that happened before this archive existed, and creating a row for it would put a
document in the manifest that is never going to be fetched.

*It does not touch the disk.* Flagging a document deactivated or cancelled is a state change;
removing the files it left behind belongs to ``pipeline/reconcile``, whose ``enact_flags`` the
caller runs immediately after the sweep — so a cancellation observed today takes the file with
it today. If the run dies in between, nothing is lost: the flag is the document's state, not
something held in memory, and the next run's startup reconciliation enacts it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from co_docs_watcher.clock import RetentionWindow
from co_docs_watcher.errors import (
    IllegalTransitionError,
    ManifestError,
    SourceContractError,
)
from co_docs_watcher.manifest.repo import Identity, Manifest
from co_docs_watcher.models import LocalState, SourceDocument, SourceStatus
from co_docs_watcher.scope.models import WatchedCompany
from co_docs_watcher.source import Source

__all__ = ["DiscoveryOutcome", "archive_everything", "discover"]

logger = logging.getLogger(__name__)

#: What the source's status means for a document the manifest already holds.
_FLAG_FOR_STATUS = {
    SourceStatus.INACTIVE: LocalState.DEACTIVATED,
    SourceStatus.CANCELLED: LocalState.CANCELLED,
}

#: States a document can leave to rejoin the queue when it is active and wanted again.
_REQUEUEABLE = frozenset({LocalState.SKIPPED, LocalState.DEACTIVATED})


def archive_everything(document: SourceDocument) -> bool:
    """The Phase 0 criteria: everything an active watched company publishes is archived.

    Criteria are a predicate rather than a constant so that a document rejected by them lands
    in ``skipped`` instead of being forgotten — and ``skipped`` is re-evaluated on every run,
    which is what lets a narrower archive be widened later without losing the days in between.
    """
    return True


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    """What one sweep did to the manifest."""

    observed: int
    ignored: int
    out_of_window: int
    unknown_inactive: int
    unchanged: int
    refused: int
    queued: tuple[Identity, ...]
    skipped: tuple[Identity, ...]
    deactivated: tuple[Identity, ...]
    cancelled: tuple[Identity, ...]

    @property
    def watched(self) -> int:
        """Rows that belong to a watched company and fell inside the window."""
        return self.observed - self.ignored - self.out_of_window


def discover(
    source: Source,
    manifest: Manifest,
    *,
    window: RetentionWindow,
    retention_window: RetentionWindow,
    watched: Iterable[WatchedCompany],
    criteria: Callable[[SourceDocument], bool] = archive_everything,
) -> DiscoveryOutcome:
    """Sweep the discovery window, filter it against the watch list, and merge what is left.

    ``retention_window`` comes alongside because the watermark is measured against it, not
    against the days this sweep happened to cover: a sweep narrower than retention observes
    nothing about the days it skipped, and asserting completed progress over them would
    disarm the only gap alarm the system has.
    """
    codes = {company.cvm_code for company in watched}
    if not codes:
        logger.warning("the watch list is empty; nothing to discover")
        return DiscoveryOutcome(0, 0, 0, 0, 0, 0, (), (), (), ())

    state = _Merge(manifest, criteria, codes=codes, window=window)
    for document in source.list_window(window.dates_newest_first):
        state.observe(document)

    _record_sweep(manifest, window, retention_window)
    outcome = state.outcome()
    logger.info(
        "discovery: %d rows, %d for watched companies, %d queued, %d deactivated, %d cancelled",
        outcome.observed,
        outcome.watched,
        len(outcome.queued),
        len(outcome.deactivated),
        len(outcome.cancelled),
    )
    return outcome


class _Merge:
    """One sweep's rules and its running result.

    Every row of the listing enters at :meth:`observe` and every count is kept here, so that
    reading this class is reading what a sweep does to the manifest.
    """

    def __init__(
        self,
        manifest: Manifest,
        criteria: Callable[[SourceDocument], bool],
        *,
        codes: set[str],
        window: RetentionWindow,
    ) -> None:
        self._manifest = manifest
        self._criteria = criteria
        self._codes = codes
        self._window = window
        self.observed = 0
        self.ignored = 0
        self.out_of_window = 0
        self.unknown_inactive = 0
        self.unchanged = 0
        self.refused = 0
        self.queued: list[Identity] = []
        self.skipped: list[Identity] = []
        self.deactivated: list[Identity] = []
        self.cancelled: list[Identity] = []

    def observe(self, document: SourceDocument) -> None:
        """Count one row of the listing, and apply to it the rule its status calls for.

        A row the manifest refuses is recorded and skipped rather than ending the sweep: an
        isolated failure never kills the batch, and a sweep that dies on row 40 of 450 leaves
        the day half-observed with nothing saying which half. The one thing not isolated is a
        divergence in the wire format — ``SourceContractError`` is fatal to the batch on
        purpose, because a partially understood payload is worse than none.
        """
        self.observed += 1
        if document.cvm_code not in self._codes:
            self.ignored += 1
            return
        if not self._window.contains(document.delivery_date):
            # The delivery date is the archive's axis, and the sweep asked for these days by
            # date: a row outside them would be filed where purge is about to delete it.
            logger.warning(
                "document %s was delivered on %s, outside the queried window %s..%s; ignored",
                document.identity,
                document.delivery_date,
                self._window.first,
                self._window.last,
            )
            self.out_of_window += 1
            return
        try:
            self._merge(document)
        except ManifestError as error:
            logger.error(
                "document %s was not merged into the manifest: %s", document.identity, error
            )
            self.refused += 1

    def _merge(self, document: SourceDocument) -> None:
        """Route one watched, in-window row to the rule that applies to its status."""
        if document.status is SourceStatus.ACTIVE:
            self.merge_active(document)
            return
        flag = _FLAG_FOR_STATUS.get(document.status)
        if flag is None:
            # The table is exhaustive over every status this build knows. A fourth one is the
            # wire format having changed under us, and that is never a silent pass.
            raise SourceContractError(
                f"document {document.identity} arrived with status {document.status}, which "
                "this build has no local state for"
            )
        self.merge_flagged(document, flag)

    def merge_active(self, document: SourceDocument) -> None:
        """Insert or refresh an active document, and re-evaluate it against the criteria."""
        documents = self._manifest.documents
        wanted = self._criteria(document)
        known = documents.get(document.identity) is not None
        initial = LocalState.DISCOVERED if wanted else LocalState.SKIPPED
        record = documents.upsert_observed(document, initial_state=initial)

        if not known:
            (self.queued if wanted else self.skipped).append(record.identity)
            return

        current = record.local_state
        if wanted and current in _REQUEUEABLE:
            # Criteria widened, or the source re-activated what it had superseded.
            documents.transition(record.identity, LocalState.DISCOVERED)
            self.queued.append(record.identity)
        elif not wanted and current is LocalState.DISCOVERED:
            # Criteria narrowed before the document was ever fetched.
            documents.transition(record.identity, LocalState.SKIPPED)
            self.skipped.append(record.identity)
        else:
            self.unchanged += 1

    def merge_flagged(self, document: SourceDocument, flag: LocalState) -> None:
        """Reconcile a document the source no longer serves under its previous status."""
        documents = self._manifest.documents
        existing = documents.get(document.identity)
        if existing is None:
            # Not archived and not created: a supersession this archive never held.
            self.unknown_inactive += 1
            return

        documents.upsert_observed(document)
        if existing.local_state is flag:
            self.unchanged += 1
            return
        try:
            documents.transition(document.identity, flag)
        except IllegalTransitionError:
            logger.warning(
                "document %s is %s locally and the source now reports %s; left as it is",
                document.identity,
                existing.local_state,
                document.status,
            )
            self.unchanged += 1
            return
        (self.deactivated if flag is LocalState.DEACTIVATED else self.cancelled).append(
            document.identity
        )

    def outcome(self) -> DiscoveryOutcome:
        return DiscoveryOutcome(
            observed=self.observed,
            ignored=self.ignored,
            out_of_window=self.out_of_window,
            unknown_inactive=self.unknown_inactive,
            unchanged=self.unchanged,
            refused=self.refused,
            queued=tuple(self.queued),
            skipped=tuple(self.skipped),
            deactivated=tuple(self.deactivated),
            cancelled=tuple(self.cancelled),
        )


def _record_sweep(
    manifest: Manifest, window: RetentionWindow, retention_window: RetentionWindow
) -> None:
    """Advance the watermark for a sweep that covered retention; only read it for one that
    did not.

    The watermark never feeds the interval — every run queries its whole window regardless.
    It exists so that a gap can be *noticed*, and staleness is always measured against the
    **retention** frontier: those are the days the archive promises to hold, whatever this
    particular sweep asked for. It reuses ``sync_state``'s ``last_completed_sweep`` rather
    than adding a key of its own because the meaning is unchanged — everything through this
    date has been observed — and a second key would give one question two answers.

    Only a sweep covering the retention window may write it. A narrower sweep observes
    nothing about the days it skipped, and recording its last day would assert "everything
    through today has been observed" over days that were never asked about — a gap alarm
    permanently green over a rotting window. Whether the sweep covered retention is derived
    from the windows, never from a profile flag. The intended consequence: stop running the
    full sweep and the warning below fires every day — turning off full coverage is a
    visible decision, not a silent omission.
    """
    previous = manifest.state.watermark()
    if previous is not None and previous < retention_window.first:
        logger.warning(
            "the last completed sweep was %s, before the retention window starts (%s): "
            "%d day(s) were never observed and are not in this archive",
            previous,
            retention_window.first,
            (retention_window.first - previous).days,
        )
    if window.first <= retention_window.first:
        manifest.state.set_watermark(window.last)
