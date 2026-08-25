"""From what a human typed to an entry in the watch list.

One function, and the two refusals that matter: nothing found, and more than one thing found.
Both leave the watch list untouched. Registering the wrong company is worse than registering
none, because the mistake shows up as a folder of documents that look plausible.

Narrowing several candidates down to one is a decision, and this module never makes it. What
it accepts is a ``Chooser``: a callable that puts the decision to whoever is running the
command. Given none — a run from cron, a pipe, any caller with nobody to ask — the ambiguity
is refused with the candidates attached, which is the same answer by another route.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from co_docs_watcher.cvm.registry import Registry, RegistryRecord
from co_docs_watcher.cvm.search import SearchResult, search
from co_docs_watcher.cvm.ticker import company_prefix
from co_docs_watcher.errors import AmbiguousQueryError, CompanyError
from co_docs_watcher.scope.models import WatchedCompany

__all__ = ["Chooser", "describe", "resolve"]

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


def describe(record: RegistryRecord) -> str:
    """One line identifying a company, for a human choosing between candidates."""
    codes = ", ".join(record.trading_codes) or "no trading code"
    return f"{record.cvm_code}  {record.legal_name}  ({codes})"
