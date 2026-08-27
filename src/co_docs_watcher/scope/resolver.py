"""From what a human typed to an entry in the watch list — and the entry kept current.

Resolving is one function, and the two refusals that matter: nothing found, and more than
one thing found. Both leave the watch list untouched. Registering the wrong company is worse
than registering none, because the mistake shows up as a folder of documents that look
plausible.

Narrowing several candidates down to one is a decision, and this module never makes it. What
it accepts is a ``Chooser``: a callable that puts the decision to whoever is running the
command. Given none — a run from cron, a pipe, any caller with nobody to ask — the ambiguity
is refused with the candidates attached, which is the same answer by another route.

Settling is the other half: a company that changes its trading code or its legal name is
followed rather than frozen at what it was called when it was registered. What survives a
rename in the registry is the previous *name*, never the previous *code* — the old code
simply disappears from the newer yearly package — so the entry is re-derived from the record
its CVM code names now, and only the fields the registry answers for move.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from co_docs_watcher.cvm.registry import Registry, RegistryRecord
from co_docs_watcher.cvm.search import SearchResult, search
from co_docs_watcher.cvm.ticker import company_prefix
from co_docs_watcher.errors import AmbiguousQueryError, CompanyError
from co_docs_watcher.scope.models import WatchedCompany
from co_docs_watcher.scope.store import WatchList

__all__ = ["Chooser", "describe", "resolve", "settle"]

logger = logging.getLogger(__name__)

#: Asked to settle an ambiguous query, given the query as typed and everything it matched.
#: It returns the chosen record; declining to choose is signalled by raising, so that a
#: caller who abandons the resolution says so in its own vocabulary instead of reading as a
#: caller that was never there.
Chooser = Callable[[str, SearchResult], RegistryRecord]


def resolve(
    registry: Registry,
    query: str,
    *,
    overrides: Mapping[str, str] | None = None,
    choose: Chooser | None = None,
) -> WatchedCompany:
    """Resolve a query into the entry that would be written to the watch list."""
    result = search(registry, query)
    if not result:
        raise CompanyError(
            f"no company in the registry matches {query!r}; try a ticker, a CNPJ, a CVM code, "
            "or part of the legal name"
        )
    record = result.only
    if record is None:
        if choose is None:
            raise AmbiguousQueryError(
                f"{query!r} matches {len(result.matches)} companies by {result.kind}; "
                "narrow it down with a ticker, a CNPJ or a CVM code",
                candidates=[describe(candidate) for candidate in result.matches],
            )
        record = choose(query, result)
    prefix = company_prefix(record, overrides=overrides)
    return WatchedCompany(
        cvm_code=record.cvm_code,
        prefix=prefix.value,
        prefix_source=prefix.source,
        legal_name=record.legal_name,
        matched_by=result.kind,
    )


def settle(
    watch_list: WatchList,
    registry: Registry,
    *,
    overrides: Mapping[str, str] | None = None,
) -> bool:
    """Re-derive every entry from the registry, so the list follows a renamed company.

    Keyed on the CVM code, the one identifier a rename does not touch. ``prefix``,
    ``prefix_source`` and ``legal_name`` are rewritten from the record; ``matched_by``
    records how the company was *found* and stays as it was. The prefix goes through the
    same override-first chain ``add`` uses, so an entry named by ``[prefix_overrides]``
    re-derives to the operator's own value and a deliberate override is never overwritten
    by a ticker change.

    A company the registry does not carry is left exactly as it is: absence from a yearly
    package means the company did not file that year, never that it stopped existing.

    A prefix that moves changes where documents land, so it is logged ``WARNING``; a legal
    name that moves alone is news for the human reading the file, logged ``INFO``. Returns
    whether anything changed; saving is the caller's, so its hash guard stays one decision.
    """
    changed = False
    for company in watch_list.companies:
        record = registry.by_cvm_code(company.cvm_code)
        if record is None:
            continue
        prefix = company_prefix(record, overrides=overrides)
        settled = WatchedCompany(
            cvm_code=company.cvm_code,
            prefix=prefix.value,
            prefix_source=prefix.source,
            legal_name=record.legal_name,
            matched_by=company.matched_by,
        )
        if settled == company:
            continue
        if settled.prefix != company.prefix:
            logger.warning(
                "watch list: %s (%s) moves from %s/ to %s/; days already archived keep %s/",
                company.cvm_code,
                settled.legal_name,
                company.prefix,
                settled.prefix,
                company.prefix,
            )
        elif settled.legal_name != company.legal_name:
            logger.info(
                "watch list: %s is now %r (was %r)",
                company.cvm_code,
                settled.legal_name,
                company.legal_name,
            )
        else:
            logger.info(
                "watch list: %s keeps the prefix %s, now named by %s (was %s)",
                company.cvm_code,
                settled.prefix,
                settled.prefix_source,
                company.prefix_source,
            )
        watch_list.update(settled)
        changed = True
    return changed


def describe(record: RegistryRecord) -> str:
    """One line identifying a company, for a human choosing between candidates."""
    codes = ", ".join(record.trading_codes) or "no trading code"
    return f"{record.cvm_code}  {record.legal_name}  ({codes})"
