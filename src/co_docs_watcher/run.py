"""One run, start to finish — and the composition root.

This is the only module in ``src/`` allowed to import ``rad/``: something has to build the
adapter and hand it to the pipeline as a ``Source``, and it happens here, once, in
:func:`open_source`. The architecture test carries that one-item allowlist.

The seven steps run in a fixed order — **lock → reconcile → registry → discover → fetch →
purge → inbox** — and the order is the design: reconciliation first so the run inherits a
consistent archive, discovery's flags enacted immediately after the sweep so a cancellation
observed today takes the file with it today, and the inbox last so it indexes what this run
actually left on disk.

Failure is graded, never all-or-nothing. A registry that cannot be refreshed blocks new
registrations and nothing else; a document that cannot be fetched is recorded and skipped; a
source that refuses the run — captcha, or the request budget burning out — stops the network
work but still lets purge and inbox run, because neither needs the network and both keep the
archive truthful. Only the captcha aborts the process with a code of its own: there is no
legitimate workaround, and the operator has to hear about it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from co_docs_watcher.clock import Clock, RetentionWindow, window_ending
from co_docs_watcher.config import Config
from co_docs_watcher.cvm.cache import RegistryCache
from co_docs_watcher.cvm.registry import Registry
from co_docs_watcher.errors import (
    ExitCode,
    RegistryError,
    RequestBudgetExceededError,
    TransientSourceError,
    WatchListConflictError,
    WatchListError,
)
from co_docs_watcher.lock import RunLock
from co_docs_watcher.manifest.db import open_manifest
from co_docs_watcher.manifest.repo import Manifest
from co_docs_watcher.models import SourceDocument
from co_docs_watcher.pipeline import (
    DiscoveryOutcome,
    FetchOutcome,
    InboxOutcome,
    PurgeOutcome,
    ReconcileOutcome,
    archive_everything,
    discover,
    enact_flags,
    fetch_pending,
    purge,
    reconcile,
    regenerate,
)
from co_docs_watcher.rad import RadClient, RadSource
from co_docs_watcher.rad.schema import parse_listing
from co_docs_watcher.scope.models import WatchedCompany
from co_docs_watcher.scope.resolver import settle
from co_docs_watcher.scope.store import WatchList
from co_docs_watcher.source import Source

__all__ = ["RunReport", "execute_run", "open_source", "probe_source"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunReport:
    """What one run did, step by step.

    The CLI reads it twice and writes nothing back: once for the exit code, and once for the
    table :mod:`co_docs_watcher.summary` renders out of it. Every step's outcome is kept whole
    rather than reduced to a count here, because the two readers ask different questions of
    the same run and neither of them is the one to decide what the other may still see.
    """

    retention_window: RetentionWindow
    discovery_window: RetentionWindow
    reconciled: ReconcileOutcome
    registry_error: str | None
    discovery: DiscoveryOutcome | None
    fetch: FetchOutcome | None
    purged: PurgeOutcome
    inbox: InboxOutcome
    interrupted: str | None

    @property
    def clean(self) -> bool:
        """Whether nothing at all went wrong — the difference between exit 0 and exit 1.

        A row the manifest refused, a date directory that would not be deleted and an index
        that could not be written are isolated failures like any other: the run carried on,
        and the exit code is what says it was not a clean one.
        """
        return (
            self.registry_error is None
            and self.interrupted is None
            and not self.reconciled.failed
            and self.discovery is not None
            and not self.discovery.refused
            and self.fetch is not None
            and not self.fetch.failed
            and not self.fetch.retrying
            and not self.purged.unremoved_dates
            and not self.inbox.refused
        )

    @property
    def exit_code(self) -> ExitCode:
        return ExitCode.CLEAN if self.clean else ExitCode.PARTIAL_FAILURE


@contextmanager
def open_source(config: Config) -> Iterator[Source]:
    """Build the concrete source. The single place the adapter is instantiated.

    Listing and download share one client on purpose: the minimum interval and the request
    budget only mean something if both halves draw from the same account.
    """
    with RadClient(
        base_url=config.source_base_url,
        min_request_interval=config.min_request_interval,
        max_requests_per_run=config.max_requests_per_run,
        retries=config.retries,
        backoff_initial=config.backoff_initial,
        backoff_factor=config.backoff_factor,
        max_listing_bytes=config.max_listing_bytes,
        max_download_bytes=config.max_download_bytes,
    ) as client:
        yield RadSource(
            client,
            max_extracted_bytes=config.max_extracted_bytes,
            reading_pdf=config.fre_reading_pdf,
        )


def probe_source(config: Config) -> str:
    """One listing request for today, for ``doctor``: reachability, measured, not assumed.

    Raises what the source raises — a captcha demand ends ``doctor`` with exit code 4 exactly
    as it would end a run. A backend failure is not retried: one request is a probe, four is a
    contribution to the outage.
    """
    clock = Clock.installed()
    today = clock.today()
    with RadClient(
        base_url=config.source_base_url,
        min_request_interval=config.min_request_interval,
        max_requests_per_run=1,
        max_listing_bytes=config.max_listing_bytes,
        retries=0,
    ) as client:
        listing = client.list_documents(today)
    return f"answered the {today} listing with {len(parse_listing(listing))} row(s)"


def execute_run(
    config: Config,
    *,
    monitor: bool = False,
    source: Source | None = None,
    clock: Clock | None = None,
    criteria: Callable[[SourceDocument], bool] = archive_everything,
) -> RunReport:
    """One run: the seven steps, in order, under the lock.

    ``monitor`` selects which configured integer becomes the discovery window and nothing
    else: the sweep covers ``discovery.monitor_days`` instead of ``discovery.days``, and the
    other six steps are byte-identical between the profiles. The retention window keeps
    driving purge and the inbox, and both windows end on the same ``today``, read once.

    ``source`` and ``clock`` are injectable for tests; the CLI passes neither, which is what
    makes this module the composition root. Raises ``LockHeldError`` (exit 3) without touching
    anything, and ``CaptchaRequiredError`` (exit 4) after putting the queue back in order.
    """
    clock = Clock.installed() if clock is None else clock
    today = clock.today()
    retention_window = window_ending(today, config.retention_days)
    discovery_window = window_ending(today, config.sweep_days(monitor=monitor))
    logger.info(
        "run: discovery window %s..%s (%d dates), retention window %s..%s (%d dates)",
        discovery_window.first,
        discovery_window.last,
        discovery_window.days,
        retention_window.first,
        retention_window.last,
        retention_window.days,
    )

    with RunLock(config.lock_path):
        connection = open_manifest(config.manifest_path)
        try:
            manifest = Manifest.over(connection, clock)
            reconciled = reconcile(
                manifest,
                documents_root=config.documents_root,
                staging_root=config.staging_root,
                max_attempts=config.max_document_attempts,
            )
            registry, registry_error = _refresh_registry(config, clock)
            watched = _settled_companies(config, registry)

            if source is None:
                with open_source(config) as built:
                    discovery, fetched, interrupted = _observe_and_fetch(
                        built, manifest, config=config, window=discovery_window,
                        retention_window=retention_window,
                        watched=watched, criteria=criteria,
                    )
            else:
                discovery, fetched, interrupted = _observe_and_fetch(
                    source, manifest, config=config, window=discovery_window,
                    retention_window=retention_window,
                    watched=watched, criteria=criteria,
                )

            purged = purge(
                manifest,
                documents_root=config.documents_root,
                inbox_root=config.inbox_root,
                window=retention_window,
            )
            inbox = regenerate(
                manifest,
                inbox_root=config.inbox_root,
                window=retention_window,
                modes=config.archive_modes,
            )
        finally:
            connection.close()

    report = RunReport(
        retention_window=retention_window,
        discovery_window=discovery_window,
        reconciled=reconciled,
        registry_error=registry_error,
        discovery=discovery,
        fetch=fetched,
        purged=purged,
        inbox=inbox,
        interrupted=interrupted,
    )
    logger.log(
        logging.INFO if report.clean else logging.WARNING,
        "run: finished %s",
        "clean" if report.clean else "with isolated failures",
    )
    return report


def _observe_and_fetch(
    source: Source,
    manifest: Manifest,
    *,
    config: Config,
    window: RetentionWindow,
    retention_window: RetentionWindow,
    watched: tuple[WatchedCompany, ...],
    criteria: Callable[[SourceDocument], bool],
) -> tuple[DiscoveryOutcome | None, FetchOutcome | None, str | None]:
    """The two steps that talk to the source, sharing its refusals.

    A refused run — the request budget burning out, or every listing attempt failing — ends
    the network work but not the run: purge and inbox still owe the archive their pass. The
    captcha is not caught anywhere: it propagates, and the process exits 4.
    """
    discovery: DiscoveryOutcome | None = None
    fetched: FetchOutcome | None = None
    try:
        discovery = discover(
            source,
            manifest,
            window=window,
            retention_window=retention_window,
            watched=watched,
            criteria=criteria,
        )
    except (RequestBudgetExceededError, TransientSourceError) as error:
        logger.log(error.severity, "discovery did not complete: %s", error)
        return discovery, fetched, str(error)

    # The same enactment reconciliation performs, run again immediately: a cancellation
    # observed by this sweep takes its file with it today, not on the next start.
    enact_flags(manifest, documents_root=config.documents_root)

    try:
        fetched = fetch_pending(
            source,
            manifest,
            documents_root=config.documents_root,
            staging_root=config.staging_root,
            watched=watched,
            max_attempts=config.max_document_attempts,
            modes=config.archive_modes,
        )
    except RequestBudgetExceededError as error:
        # The queue was already put back in order by the fetch step itself.
        logger.warning("fetch did not complete: %s", error)
        return discovery, fetched, str(error)
    return discovery, fetched, None


def _refresh_registry(config: Config, clock: Clock) -> tuple[Registry | None, str | None]:
    """Refresh the FCA cache if stale. Failure blocks new registrations, never monitoring.

    The watch list persists the resolved prefix of every company, so a run needs no registry
    at all — what a failed refresh costs is ``add``, and the run says so instead of dying.
    """
    cache = RegistryCache(
        config.registry_cache_root, max_age_days=config.registry_max_age_days
    )
    try:
        return cache.load(now=clock.now()), None
    except RegistryError as error:
        logger.warning(
            "registry: %s; monitoring continues on the watch list alone, "
            "but `add` will refuse until a package can be fetched",
            error,
        )
        return None, str(error)


def _settled_companies(config: Config, registry: Registry | None) -> tuple[WatchedCompany, ...]:
    """The watch list this run monitors, settled against the registry it just refreshed.

    Settling is part of the registry step, not a step of its own, and it inherits the step's
    grade of failure: no usable registry means no update and a run that monitors normally on
    what the file already says. A save the hash guard refuses is a human edit made while the
    run held the lock — the edit wins, the list is reloaded, and this run monitors what the
    human wrote; a file that cannot be written at all costs only the persistence, since the
    settled entries are already in memory and the next run derives them again.
    """
    watch_list = WatchList.load(config.watch_list_path)
    if registry is None or not settle(
        watch_list, registry, overrides=config.prefix_overrides
    ):
        return watch_list.companies
    try:
        watch_list.save()
    except WatchListConflictError:
        return WatchList.load(config.watch_list_path).companies
    except WatchListError as error:
        logger.warning(
            "watch list: the settled entries could not be written (%s); this run uses them "
            "and the next one derives them again",
            error,
        )
    return watch_list.companies
