"""The search chain: precedence, the previous legal name, and refusing to choose."""

from __future__ import annotations

from co_docs_watcher.cvm.registry import Registry, parse_package
from co_docs_watcher.cvm.search import MatchKind, search
from tests import fca

ENERGISA_MT = {
    "CNPJ_Companhia": "03.467.321/0001-99",
    "Versao": "1",
    "ID_Documento": "158180",
    "Nome_Empresarial": "ENERGISA MATO GROSSO-DISTRIBUIDORA DE ENERGIA S/A",
    "Nome_Empresarial_Anterior": "Centrais Elétricas Matogrossenses S.A - Cemat",
    "Codigo_CVM": "014605",
    "Situacao_Registro_CVM": "Ativo",
}


def registry(**overrides: object) -> Registry:
    return parse_package(fca.build_package(**overrides))  # type: ignore[arg-type]


def test_a_trading_code_finds_its_company() -> None:
    result = search(registry(), "PETR4")

    assert result.kind is MatchKind.TICKER
    assert result.only is not None
    assert result.only.cvm_code == "009512"


def test_the_root_finds_every_class_of_the_same_company() -> None:
    # PETR3 and PETR4 are one company; typing the root must not be ambiguous.
    result = search(registry(), "petr")

    assert result.kind is MatchKind.TICKER
    assert result.only is not None
    assert result.only.legal_name == "PETROLEO BRASILEIRO S.A. PETROBRAS"


def test_a_cnpj_is_matched_in_either_spelling() -> None:
    formatted = search(registry(), "33.592.510/0001-54")
    bare = search(registry(), "33592510000154")

    assert formatted.kind is MatchKind.CNPJ
    assert formatted.only == bare.only


def test_a_cvm_code_is_matched_with_or_without_padding() -> None:
    for query in ("004170", "4170", "00417-0"):
        result = search(registry(), query)
        assert result.kind is MatchKind.CVM_CODE
        assert result.only is not None
        assert result.only.legal_name == "VALE S.A."


def test_a_substring_of_the_legal_name_is_the_first_fallback() -> None:
    result = search(registry(), "plascar")

    assert result.kind is MatchKind.LEGAL_NAME
    assert result.only is not None
    assert result.only.cvm_code == "013471"


def test_a_company_found_only_by_the_name_it_dropped() -> None:
    # The name a person remembers is very often the one the company stopped using.
    result = search(registry(), "rio doce")

    assert result.kind is MatchKind.PREVIOUS_LEGAL_NAME
    assert result.only is not None
    assert result.only.legal_name == "VALE S.A."


def test_the_name_search_ignores_accents_and_case() -> None:
    result = search(registry(), "gestão logistica")

    assert result.kind is MatchKind.LEGAL_NAME
    assert result.only is not None
    assert result.only.cvm_code == "020800"


def test_an_identifier_wins_over_a_name_that_would_also_match() -> None:
    # A company whose legal name contains another company's ticker must not shadow it.
    named_petr = dict(fca.GENERAL_ROWS[1]) | {"Nome_Empresarial": "PETR PARTICIPACOES S.A."}
    result = search(registry(general=[fca.GENERAL_ROWS[0], named_petr]), "PETR")

    assert result.kind is MatchKind.TICKER
    assert result.only is not None
    assert result.only.cvm_code == "009512"


def test_every_match_of_the_answering_stage_is_returned() -> None:
    result = search(registry(general=[*fca.GENERAL_ROWS, ENERGISA_MT]), "energisa")

    assert result.kind is MatchKind.LEGAL_NAME
    assert result.is_ambiguous
    assert result.only is None
    assert {record.cvm_code for record in result.matches} == {"015253", "014605"}


def test_nothing_found_is_an_empty_result_and_not_a_guess() -> None:
    result = search(registry(), "there is no such company")

    assert not result
    assert result.kind is None
    assert result.matches == ()


def test_an_empty_query_matches_nothing() -> None:
    assert not search(registry(), "")
    assert not search(registry(), "   ")
