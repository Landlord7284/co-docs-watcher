"""From what a human typed to an entry in the watch list.

One function, and the two refusals that matter: nothing found, and more than one thing found.
Both leave the watch list untouched. Registering the wrong company is worse than registering
none, because the mistake shows up as a folder of documents that look plausible.
"""

from __future__ import annotations

from collections.abc import Mapping

from co_docs_watcher.cvm.registry import Registry, RegistryRecord
from co_docs_watcher.cvm.search import search
from co_docs_watcher.cvm.ticker import company_prefix
from co_docs_watcher.errors import AmbiguousQueryError, CompanyError
from co_docs_watcher.scope.models import WatchedCompany

__all__ = ["describe", "resolve"]


def resolve(
    registry: Registry, query: str, *, overrides: Mapping[str, str] | None = None
) -> WatchedCompany:
    """Resolve a query into the entry that would be written to the watch list."""
    result = search(registry, query)
    if not result:
        raise CompanyError(
            f"no company in the registry matches {query!r}; try a ticker, a CNPJ, a CVM code, "
            "or part of the legal name"
        )
    if result.is_ambiguous:
        raise AmbiguousQueryError(
            f"{query!r} matches {len(result.matches)} companies by {result.kind}; "
            "narrow it down with a ticker, a CNPJ or a CVM code",
            candidates=[describe(record) for record in result.matches],
        )

    record = result.matches[0]
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
