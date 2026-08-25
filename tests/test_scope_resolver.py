"""Resolving a query into a watch list entry, the two refusals, and the chooser."""

from __future__ import annotations

import pytest

from co_docs_watcher.cvm.registry import Registry, RegistryRecord, parse_package
from co_docs_watcher.cvm.search import MatchKind, SearchResult
from co_docs_watcher.cvm.ticker import PrefixSource
from co_docs_watcher.errors import AmbiguousQueryError, CompanyError
from co_docs_watcher.scope.resolver import resolve
from tests import fca
from tests.test_cvm_search import ENERGISA_MT


def registry(**overrides: object) -> Registry:
    return parse_package(fca.build_package(**overrides))  # type: ignore[arg-type]


def test_a_resolved_company_records_how_it_was_found_and_how_it_was_named() -> None:
    entry = resolve(registry(), "PETR4")

    assert entry.cvm_code == "009512"
    assert entry.prefix == "PETR"
    assert entry.prefix_source is PrefixSource.TICKER
    assert entry.matched_by is MatchKind.TICKER
    assert entry.legal_name == "PETROLEO BRASILEIRO S.A. PETROBRAS"


def test_a_company_whose_ticker_field_is_junk_is_named_after_its_legal_name() -> None:
    entry = resolve(registry(), "plascar")

    assert entry.prefix == "PLASCAR-PARTICIPACOES"
    assert entry.prefix_source is PrefixSource.LEGAL_NAME
    assert entry.matched_by is MatchKind.LEGAL_NAME


def test_an_override_reaches_the_entry_that_is_stored() -> None:
    entry = resolve(registry(), "SC303", overrides={"003549": "SCHLOSSER"})

    assert entry.prefix == "SCHLOSSER"
    assert entry.prefix_source is PrefixSource.OVERRIDE


def test_an_ambiguous_query_is_handed_back_with_its_candidates() -> None:
    ambiguous = registry(general=[*fca.GENERAL_ROWS, ENERGISA_MT])

    with pytest.raises(AmbiguousQueryError) as raised:
        resolve(ambiguous, "energisa")

    assert len(raised.value.candidates) == 2
    assert any("ENERGISA S.A." in candidate for candidate in raised.value.candidates)
    assert any("014605" in candidate for candidate in raised.value.candidates)


def test_a_chooser_settles_an_ambiguous_query() -> None:
    ambiguous = registry(general=[*fca.GENERAL_ROWS, ENERGISA_MT])
    seen: list[tuple[str, int]] = []

    def choose(query: str, result: SearchResult) -> RegistryRecord:
        seen.append((query, len(result.matches)))
        return result.matches[-1]

    entry = resolve(ambiguous, "energisa", choose=choose)

    assert seen == [("energisa", 2)]
    assert entry.cvm_code == "014605"


def test_a_chooser_is_never_asked_about_an_unambiguous_query() -> None:
    def choose(query: str, result: SearchResult) -> RegistryRecord:
        raise AssertionError("nothing to choose between")

    assert resolve(registry(), "PETR4", choose=choose).cvm_code == "009512"


def test_a_query_that_matches_nothing_says_what_would_work() -> None:
    with pytest.raises(CompanyError, match="ticker, a CNPJ, a CVM code"):
        resolve(registry(), "there is no such company")
