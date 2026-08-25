"""The root rule, the tie-break, and the three steps of the fallback chain."""

from __future__ import annotations

import pytest

from co_docs_watcher.cvm.registry import RegistryRecord
from co_docs_watcher.cvm.ticker import (
    CompanyPrefix,
    PrefixSource,
    choose_root,
    company_prefix,
    ticker_root,
)


def record(**overrides: object) -> RegistryRecord:
    fields: dict[str, object] = {
        "cvm_code": "009512",
        "cnpj": "33000167000101",
        "legal_name": "PETROLEO BRASILEIRO S.A. PETROBRAS",
        "previous_legal_name": None,
        "trading_codes": ("PETR3", "PETR4"),
        "registration_status": "Ativo",
    }
    return RegistryRecord(**(fields | overrides))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("PETR4", "PETR"),
        ("POMO3", "POMO"),
        ("POMO4", "POMO"),
        ("EQMA3B", "EQMA"),
        ("B3SA3", "B3SA"),
        # Greedy on purpose: the unit's root is not the share's, and the tie-break below is
        # what turns the pair back into one company.
        ("ENGI11", "ENGI1"),
        ("SC303", "SC30"),
        # Already a root: no class digit to strip.
        ("LMED", "LMED"),
        ("TEGA", "TEGA"),
        # The registry is inconsistent about case; a ticker is upper case by convention.
        ("tgma3", "TGMA"),
        ("  vale3 ", "VALE"),
    ],
)
def test_the_root_rule_keeps_the_company_and_drops_the_class(code: str, expected: str) -> None:
    assert ticker_root(code) == expected


@pytest.mark.parametrize(
    "junk",
    [
        "",
        " ",
        "NÃO HÁ",
        "NÃO",
        "N/A",
        "B3",
        "ADR",
        "PNC",
        "0",
        "0000",
        "000000",
        "1545-8",
        "713854",
        "22055",
        "PET",  # too short to be a root
    ],
)
def test_junk_in_the_free_text_field_is_not_a_ticker(junk: str) -> None:
    assert ticker_root(junk) is None


def test_the_shorter_root_wins_a_receipt_pair() -> None:
    assert choose_root(["ENGI3", "ENGI4", "ENGI11", "ENGI13"]) == "ENGI"
    assert choose_root(["SAPR11", "SAPR1", "SAPR3"]) == "SAPR"


def test_equally_short_roots_break_alphabetically() -> None:
    # Two ordinary-looking codes, no receipt to prefer: stability is what is left to want.
    assert choose_root(["SCL04", "SC303"]) == "SC30"
    assert choose_root(["TXRX3", "TRXR4"]) == "TRXR"


def test_a_company_with_no_usable_code_has_no_root() -> None:
    assert choose_root([]) is None
    assert choose_root(["B3", "NÃO HÁ", ""]) is None


def test_the_chain_starts_at_the_validated_ticker_root() -> None:
    assert company_prefix(record()) == CompanyPrefix("PETR", PrefixSource.TICKER)


def test_the_chain_falls_back_to_the_reduced_legal_name() -> None:
    plascar = record(
        cvm_code="013471",
        legal_name="PLASCAR PARTICIPACOES INDUSTRIAIS S.A.",
        trading_codes=("B3",),
    )

    assert company_prefix(plascar) == CompanyPrefix(
        "PLASCAR-PARTICIPACOES", PrefixSource.LEGAL_NAME
    )


def test_the_chain_ends_at_the_zero_padded_cvm_code() -> None:
    nameless = record(cvm_code="9512", legal_name="S.A.", trading_codes=())

    assert company_prefix(nameless) == CompanyPrefix("009512", PrefixSource.CVM_CODE)


def test_an_override_settles_what_the_rule_cannot() -> None:
    schlosser = record(
        cvm_code="003549",
        legal_name="CIA INDUSTRIAL SCHLOSSER S.A.",
        trading_codes=("SC303", "SCL04"),
    )

    assert company_prefix(schlosser) == CompanyPrefix("SC30", PrefixSource.TICKER)
    assert company_prefix(schlosser, overrides={"003549": "SCHLOSSER"}) == CompanyPrefix(
        "SCHLOSSER", PrefixSource.OVERRIDE
    )


def test_an_override_for_another_company_changes_nothing() -> None:
    assert company_prefix(record(), overrides={"003549": "SCHLOSSER"}).source is PrefixSource.TICKER
