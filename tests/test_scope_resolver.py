"""Resolving a query into a watch list entry, the refusals, the chooser — and settling."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from co_docs_watcher.cvm.registry import Registry, RegistryRecord, parse_package
from co_docs_watcher.cvm.search import MatchKind, SearchResult
from co_docs_watcher.cvm.ticker import PrefixSource
from co_docs_watcher.errors import AmbiguousQueryError, CompanyError
from co_docs_watcher.scope.resolver import resolve, settle
from co_docs_watcher.scope.store import WatchList
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


# --- Settling: the entry follows the registry, keyed on the code a rename does not touch. ---

WATCHED_ODONTOPREV = """\
# The one I actually read every morning.
companies:
  - cvm_code: '020125'
    prefix: ODPV
    prefix_source: ticker
    matched_by: cnpj
    legal_name: ODONTOPREV S.A.
"""


def watch_list(tmp_path: Path, content: str = WATCHED_ODONTOPREV) -> WatchList:
    path = tmp_path / "companies.yaml"
    path.write_text(content, encoding="utf-8")
    return WatchList.load(path)


def registry_before_the_rename() -> Registry:
    return registry(
        general=[*fca.GENERAL_ROWS, fca.ODONTOPREV_GENERAL_2025],
        securities=[*fca.SECURITIES_ROWS, fca.ODONTOPREV_SECURITIES_2025],
    )


def registry_after_the_rename() -> Registry:
    return registry(
        general=[*fca.GENERAL_ROWS, fca.BRADSAUDE_GENERAL_2026],
        securities=[*fca.SECURITIES_ROWS, fca.BRADSAUDE_SECURITIES_2026],
    )


def test_a_renamed_company_re_derives_and_keeps_how_it_was_found(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    watched = watch_list(tmp_path)

    with caplog.at_level(logging.INFO):
        assert settle(watched, registry_after_the_rename())

    (entry,) = watched.companies
    assert entry.prefix == "SAUD"
    assert entry.prefix_source is PrefixSource.TICKER
    assert entry.legal_name == "BRADSAÚDE S.A."
    # How the company was *found* is a record, not a derivation: it stays.
    assert entry.matched_by is MatchKind.CNPJ
    # A prefix that moves changes where documents land, and is warned about.
    moved = [record for record in caplog.records if "moves from ODPV/ to SAUD/" in record.message]
    assert moved and moved[0].levelno == logging.WARNING


def test_an_entry_named_by_an_override_re_derives_to_the_override(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A deliberate override is never overwritten by a ticker change; only the legal name
    follows the registry, which is news for a human and not for the archive."""
    watched = watch_list(
        tmp_path,
        WATCHED_ODONTOPREV.replace("prefix: ODPV", "prefix: DENTAL").replace(
            "prefix_source: ticker", "prefix_source: override"
        ),
    )

    with caplog.at_level(logging.INFO):
        assert settle(watched, registry_after_the_rename(), overrides={"020125": "DENTAL"})

    (entry,) = watched.companies
    assert entry.prefix == "DENTAL"
    assert entry.prefix_source is PrefixSource.OVERRIDE
    assert entry.legal_name == "BRADSAÚDE S.A."
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert any("is now" in record.message for record in caplog.records)


def test_a_company_the_registry_does_not_carry_is_left_alone(tmp_path: Path) -> None:
    watched = watch_list(tmp_path)
    before = watched.companies

    assert not settle(watched, registry())

    assert watched.companies == before


def test_a_list_that_already_agrees_is_untouched(tmp_path: Path) -> None:
    watched = watch_list(tmp_path)

    assert not settle(watched, registry_before_the_rename())


def test_settling_survives_the_round_trip_with_the_comments_intact(tmp_path: Path) -> None:
    watched = watch_list(tmp_path)

    assert settle(watched, registry_after_the_rename())
    watched.save()

    content = watched.path.read_text(encoding="utf-8")
    assert "# The one I actually read every morning." in content
    assert "prefix: SAUD" in content
    assert "matched_by: cnpj" in content
