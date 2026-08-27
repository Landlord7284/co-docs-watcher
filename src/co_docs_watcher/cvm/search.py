"""Finding a company in the registry from whatever a human happens to know about it.

The chain is ordered by how much a match means, not by how likely it is: an exact identifier
first — ticker, then ``CNPJ``, then CVM code — and only then a substring of a name. The first
stage that matches anything wins, and it returns *everything* it matched. Narrowing two
candidates down to one is a decision, and decisions belong to the human running ``add``.

Both names are searched, current and previous. It reads like completeness and is not: 495 of
the 675 companies in the 2026 registry carry a previous legal name (2026-08-24), and the name
a person remembers is very often the one the company dropped.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from co_docs_watcher.cvm.registry import Registry, RegistryRecord
from co_docs_watcher.cvm.ticker import ticker_root
from co_docs_watcher.text import normalize_cnpj, normalize_cvm_code, normalize_key

__all__ = ["MatchKind", "SearchResult", "search"]

#: What a CVM code may be typed as: digits, with or without the listing's hyphen.
_CVM_CODE_QUERY = re.compile(r"^\d{1,6}$|^\d{1,5}-\d$")

#: One stage of the chain: a registry and a query in, everything that stage matched out.
_Stage = Callable[[Registry, str], tuple[RegistryRecord, ...]]


class MatchKind(StrEnum):
    """Which stage of the chain answered. Recorded in the watch list, because a company found
    by a substring of a name it no longer uses deserves to be re-checked by a human later."""

    TICKER = "ticker"
    CNPJ = "cnpj"
    CVM_CODE = "cvm_code"
    LEGAL_NAME = "legal_name"
    PREVIOUS_LEGAL_NAME = "previous_legal_name"


@dataclass(frozen=True, slots=True)
class SearchResult:
    """What the chain found, and where."""

    kind: MatchKind | None
    matches: tuple[RegistryRecord, ...]

    def __bool__(self) -> bool:
        return bool(self.matches)

    @property
    def is_ambiguous(self) -> bool:
        return len(self.matches) > 1

    @property
    def only(self) -> RegistryRecord | None:
        """The single match, or ``None`` when there is none or more than one."""
        return self.matches[0] if len(self.matches) == 1 else None


def search(registry: Registry, query: str) -> SearchResult:
    """Walk the chain and return the first stage that matched anything."""
    for kind, stage in _CHAIN:
        matches = stage(registry, query)
        if matches:
            return SearchResult(kind, matches)
    return SearchResult(None, ())


def _by_ticker(registry: Registry, query: str) -> tuple[RegistryRecord, ...]:
    """Match a trading code or the root behind it: ``PETR4`` and ``PETR`` find one company."""
    candidate = query.strip().upper()
    root = ticker_root(candidate)
    if root is None:
        return ()
    return tuple(
        record
        for record in registry
        if candidate in record.trading_codes
        or any(ticker_root(code) == root for code in record.trading_codes)
    )


def _by_cnpj(registry: Registry, query: str) -> tuple[RegistryRecord, ...]:
    cnpj = normalize_cnpj(query)
    if not cnpj:
        return ()
    record = registry.by_cnpj(cnpj)
    return (record,) if record else ()


def _by_cvm_code(registry: Registry, query: str) -> tuple[RegistryRecord, ...]:
    if not _CVM_CODE_QUERY.match(query.strip()):
        return ()
    record = registry.by_cvm_code(normalize_cvm_code(query))
    return (record,) if record else ()


def _by_legal_name(registry: Registry, query: str) -> tuple[RegistryRecord, ...]:
    key = normalize_key(query)
    if not key:
        return ()
    return tuple(record for record in registry if key in normalize_key(record.legal_name))


def _by_previous_legal_name(registry: Registry, query: str) -> tuple[RegistryRecord, ...]:
    key = normalize_key(query)
    if not key:
        return ()
    return tuple(
        record
        for record in registry
        if record.previous_legal_name and key in normalize_key(record.previous_legal_name)
    )


#: The chain, in the order a match means something. Held as the functions themselves rather
#: than as their results, so that walking it stops where it matches: the two name stages scan
#: every record in the registry, and a query answered by its ticker must not pay for them.
_CHAIN: tuple[tuple[MatchKind, _Stage], ...] = (
    (MatchKind.TICKER, _by_ticker),
    (MatchKind.CNPJ, _by_cnpj),
    (MatchKind.CVM_CODE, _by_cvm_code),
    (MatchKind.LEGAL_NAME, _by_legal_name),
    (MatchKind.PREVIOUS_LEGAL_NAME, _by_previous_legal_name),
)
