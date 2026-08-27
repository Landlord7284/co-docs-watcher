"""Identifier normalization and the names derived from free text."""

from __future__ import annotations

import pytest

from co_docs_watcher.text import (
    CVM_CODE_RULE,
    normalize_cnpj,
    normalize_cvm_code,
    normalize_key,
    reduce_legal_name,
    safe_component,
    strip_accents,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00951-2", "009512"),  # the listing's spelling
        ("009512", "009512"),  # the registry's spelling
        ("9512", "009512"),  # what a human types
        (9512, "009512"),
        ("  009512  ", "009512"),
        ("", ""),
        ("PETR", ""),
        ("1234567", "1234567"),  # longer than six digits: visible, not truncated
    ],
)
def test_cvm_codes_reduce_to_one_spelling(raw: str | int, expected: str) -> None:
    assert normalize_cvm_code(raw) == expected


@pytest.mark.parametrize(
    ("written", "accepted"),
    [
        ("00951-2", True),  # the regulator's own hyphenated spelling
        ("009512", True),
        ("9512", True),
        ("0", True),
        ("PETR4", False),  # normalizes to 000004: a different company entirely
        ("2026-08-24", False),  # normalizes to 20260824
        ("PETR", False),
        ("", False),
        ("1234567", False),  # longer than a CVM code
        ("00951-23", False),
    ],
)
def test_the_written_code_is_validated_before_it_is_normalized(
    written: str, accepted: bool
) -> None:
    # normalize_cvm_code drops every non-digit, so text that was never a code comes back
    # looking like one. Whoever reads a code out of a hand-written file matches this first.
    assert bool(CVM_CODE_RULE.match(written)) is accepted


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("33.000.167/0001-01", "33000167000101"),
        ("33000167000101", "33000167000101"),
        ("33.000.167/0001", ""),  # half a CNPJ matches nothing rather than the wrong company
        ("", ""),
        ("PETROBRAS", ""),
    ],
)
def test_cnpjs_are_either_fourteen_digits_or_nothing(raw: str, expected: str) -> None:
    assert normalize_cnpj(raw) == expected


def test_accents_are_stripped_without_losing_the_letters() -> None:
    assert strip_accents("PARTICIPAÇÕES") == "PARTICIPACOES"
    assert strip_accents("SÃO MARTINHO") == "SAO MARTINHO"


def test_the_search_key_makes_the_registrys_spelling_irrelevant() -> None:
    assert normalize_key("  são   martinho s.a. ") == "SAO MARTINHO S A"
    assert normalize_key("Cia. Vale do Rio Doce") == "CIA VALE DO RIO DOCE"
    assert normalize_key("---") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Fato Relevante", "FATO-RELEVANTE"),
        ("Aviso aos Acionistas", "AVISO-AOS-ACIONISTAS"),
        # Truncated at a word boundary: a name cut mid-word reads like a bug.
        ("Comunicado ao Mercado sobre a Oferta", "COMUNICADO-AO-MERCADO"),
        ("Comunicado ao Mercado!!!", "COMUNICADO-AO-MERCADO"),
        ("", ""),
        ("///", ""),
        ("SUPERCALIFRAGILISTICEXPIALIDOCIOUS", "SUPERCALIFRAGILISTICEXPI"),
    ],
)
def test_names_become_path_components(raw: str, expected: str) -> None:
    assert safe_component(raw) == expected


@pytest.mark.parametrize(
    ("legal_name", "expected"),
    [
        ("PLASCAR PARTICIPACOES INDUSTRIAIS S.A.", "PLASCAR-PARTICIPACOES"),
        ("VALE S.A.", "VALE"),
        ("CIA INDUSTRIAL SCHLOSSER S.A.", "INDUSTRIAL-SCHLOSSER"),
        ("Companhia Vale do Rio Doce", "VALE-DO-RIO-DOCE"),
        ("TEGMA GESTÃO LOGÍSTICA LTDA", "TEGMA-GESTAO-LOGISTICA"),
        ("ENERGISA MATO GROSSO-DISTRIBUIDORA DE ENERGIA S/A", "ENERGISA-MATO-GROSSO"),
        # The judicial state belongs to the situation, not to the identity.
        ("GRUPO TOKY S.A. - EM RECUPERAÇÃO JUDICIAL", "GRUPO-TOKY"),
        ("CIA S.A. LTDA", ""),
    ],
)
def test_legal_names_reduce_to_the_part_that_identifies_the_company(
    legal_name: str, expected: str
) -> None:
    assert reduce_legal_name(legal_name) == expected
