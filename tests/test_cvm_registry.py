"""Parsing the FCA package: latest version, active codes, and loud contract violations."""

from __future__ import annotations

import logging

import pytest

from co_docs_watcher.cvm.registry import Registry, RegistryRecord, merge_registries, parse_package
from co_docs_watcher.errors import RegistryError
from tests import fca


def registry() -> Registry:
    return parse_package(fca.build_package())


def test_the_recorded_package_parses_into_identity_records() -> None:
    petrobras = registry().by_cnpj(fca.PETROBRAS)

    assert petrobras == RegistryRecord(
        cvm_code="009512",
        cnpj="33000167000101",
        legal_name="PETROLEO BRASILEIRO S.A. PETROBRAS",
        previous_legal_name=None,
        trading_codes=("PETR3", "PETR4"),
        registration_status="Ativo",
    )


def test_the_previous_legal_name_survives_the_parse() -> None:
    # Half the market has one, and it is the name a human remembers a company by.
    vale = registry().by_cvm_code("004170")

    assert vale is not None
    assert vale.previous_legal_name == "Companhia Vale do Rio Doce"


def test_a_code_that_stopped_trading_is_not_an_active_code() -> None:
    energisa = registry().by_cnpj(fca.ENERGISA)

    assert energisa is not None
    assert energisa.trading_codes == ("ENGI11", "ENGI3", "ENGI4")


def test_trading_codes_are_upper_cased_and_empty_ones_dropped() -> None:
    parsed = registry()
    tegma = parsed.by_cnpj(fca.TEGMA)
    petrobras = parsed.by_cnpj(fca.PETROBRAS)

    assert tegma is not None and tegma.trading_codes == ("TGMA3",)
    # The debenture row carries no code at all; it must not become an empty ticker.
    assert petrobras is not None and "" not in petrobras.trading_codes


def test_only_the_latest_version_of_a_company_is_kept() -> None:
    older = dict(fca.GENERAL_ROWS[0]) | {"Versao": "1", "Nome_Empresarial": "OLD NAME"}
    newer = dict(fca.GENERAL_ROWS[0]) | {"Versao": "2", "ID_Documento": "160575"}
    codes = [
        {"CNPJ_Companhia": fca.PETROBRAS, "ID_Documento": "156276", "Codigo_Negociacao": "OLDX3"},
        {"CNPJ_Companhia": fca.PETROBRAS, "ID_Documento": "160575", "Codigo_Negociacao": "PETR3"},
    ]

    parsed = parse_package(fca.build_package(general=[older, newer], securities=codes))
    petrobras = parsed.by_cnpj(fca.PETROBRAS)

    assert petrobras is not None
    assert petrobras.legal_name == "PETROLEO BRASILEIRO S.A. PETROBRAS"
    # Codes are joined on the selected version, so the superseded ticker does not leak in.
    assert petrobras.trading_codes == ("PETR3",)


def test_the_cvm_code_is_normalized_and_indexed_both_ways() -> None:
    parsed = registry()

    assert parsed.by_cvm_code("9512") is parsed.by_cnpj("33.000.167/0001-01")
    assert parsed.by_cvm_code("00951-2") is parsed.by_cnpj(fca.PETROBRAS)
    assert parsed.by_cvm_code("999999") is None
    assert parsed.by_cnpj("not a cnpj") is None


def test_a_cvm_code_claimed_by_two_companies_is_logged_as_a_contract_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    twin = dict(fca.GENERAL_ROWS[1]) | {"Codigo_CVM": "009512"}

    with caplog.at_level(logging.CRITICAL):
        parsed = parse_package(fca.build_package(general=[fca.GENERAL_ROWS[0], twin]))

    assert "claimed by two CNPJs" in caplog.text
    assert parsed.by_cvm_code("009512") is not None
    assert parsed.by_cvm_code("009512").cnpj == "33000167000101"
    # The colliding record is not silently resolved away: it stays visible in the records.
    assert len(parsed.records) == 2


def test_a_cnpj_claiming_two_cvm_codes_is_logged_and_the_first_one_is_kept(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The relation is 1:1 in both directions, so the anomaly is the same one seen from the
    # other end: a CNPJ under two codes must not resolve to whichever record came last.
    first = RegistryRecord("009512", "33000167000101", "PETROBRAS", None, (), "Ativo")
    second = RegistryRecord("009999", "33000167000101", "PETROBRAS OUTRA", None, (), "Ativo")

    with caplog.at_level(logging.CRITICAL):
        parsed = Registry([first, second])

    assert "claims two CVM codes" in caplog.text
    assert parsed.by_cnpj("33000167000101") is first


def test_a_code_collision_does_not_cost_the_record_its_cnpj() -> None:
    # The colliding record leaves the code index and that one only: its CNPJ is unique and
    # has done nothing wrong, and dropping it would lose a company over another one's clash.
    held = RegistryRecord("009512", "33000167000101", "PETROBRAS", None, (), "Ativo")
    colliding = RegistryRecord("009512", "33592510000154", "VALE", None, (), "Ativo")

    parsed = Registry([held, colliding])

    assert parsed.by_cvm_code("009512") is held
    assert parsed.by_cnpj("33592510000154") is colliding


def test_a_cnpj_that_changes_cvm_code_across_versions_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = dict(fca.GENERAL_ROWS[0]) | {"Versao": "1", "ID_Documento": "156276"}
    second = dict(fca.GENERAL_ROWS[0]) | {
        "Versao": "2",
        "ID_Documento": "160575",
        "Codigo_CVM": "009999",
    }

    with caplog.at_level(logging.CRITICAL):
        parse_package(fca.build_package(general=[first, second]))

    assert "two CVM codes across versions" in caplog.text


def test_a_payload_that_is_not_a_package_is_refused() -> None:
    with pytest.raises(RegistryError, match="not a ZIP"):
        parse_package(b"<html>Servico indisponivel</html>")

    with pytest.raises(RegistryError, match="corrupt"):
        parse_package(b"PK\x03\x04 and then nothing that a reader can use")


def test_a_missing_member_is_refused_rather_than_answered_with_less() -> None:
    only_general = {
        "fca_cia_aberta_geral_2026.csv": fca.csv_member(fca.GENERAL_COLUMNS, fca.GENERAL_ROWS)
    }

    with pytest.raises(RegistryError, match="no securities member"):
        parse_package(fca.build_package(members=only_general))


def test_a_missing_column_is_refused_as_a_format_change() -> None:
    without_previous_name = [
        column for column in fca.GENERAL_COLUMNS if column != "Nome_Empresarial_Anterior"
    ]

    with pytest.raises(RegistryError, match="Nome_Empresarial_Anterior"):
        parse_package(fca.build_package(general_columns=without_previous_name))


def test_a_row_shorter_than_its_header_is_refused_as_a_format_change() -> None:
    # A column dropped from a single row leaves ``DictReader`` filling it with ``None``, and
    # every value below the parser is a string it strips: the divergence has to stop here.
    header = fca.DELIMITER.join(fca.GENERAL_COLUMNS)
    short = fca.DELIMITER.join([fca.PETROBRAS, "", "1", "156276", "PETROLEO BRASILEIRO S.A."])
    members = {
        "fca_cia_aberta_geral_2026.csv": f"{header}\r\n{short}\r\n".encode(fca.ENCODING),
        "fca_cia_aberta_valor_mobiliario_2026.csv": fca.csv_member(
            fca.SECURITIES_COLUMNS, fca.SECURITIES_ROWS
        ),
    }

    with pytest.raises(RegistryError, match="shorter than its header"):
        parse_package(fca.build_package(members=members))


def test_a_package_carrying_two_years_at_once_is_refused() -> None:
    # The join between the two members is on ``ID_Documento``: pairing the general member of
    # one year with the securities member of another answers a registry that trades nothing.
    both_years = {
        "fca_cia_aberta_geral_2025.csv": fca.csv_member(fca.GENERAL_COLUMNS, fca.GENERAL_ROWS),
        "fca_cia_aberta_geral_2026.csv": fca.csv_member(fca.GENERAL_COLUMNS, fca.GENERAL_ROWS),
        "fca_cia_aberta_valor_mobiliario_2026.csv": fca.csv_member(
            fca.SECURITIES_COLUMNS, fca.SECURITIES_ROWS
        ),
    }

    with pytest.raises(RegistryError, match="2 general members"):
        parse_package(fca.build_package(members=both_years))


def test_a_company_dropped_for_an_unusable_cvm_code_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The sweep is filtered against the code, so the company cannot be watched — but a
    # company leaving the registry in silence is the failure this parser exists to refuse.
    codeless = dict(fca.GENERAL_ROWS[0]) | {"Codigo_CVM": "n/a"}

    with caplog.at_level(logging.WARNING):
        parsed = parse_package(fca.build_package(general=[codeless, fca.GENERAL_ROWS[1]]))

    assert "CVM code is unusable" in caplog.text
    assert parsed.by_cnpj(fca.PETROBRAS) is None
    assert parsed.by_cnpj(fca.VALE) is not None


def test_an_unreadable_version_ranks_last_and_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    readable = dict(fca.GENERAL_ROWS[0]) | {"Versao": "1", "ID_Documento": "156276"}
    unreadable = dict(fca.GENERAL_ROWS[0]) | {
        "Versao": "n/a",
        "ID_Documento": "160575",
        "Nome_Empresarial": "PETROLEO BRASILEIRO S.A. PETROBRAS (NOVO)",
    }

    with caplog.at_level(logging.WARNING):
        parsed = parse_package(fca.build_package(general=[readable, unreadable]))

    assert "unreadable Versao" in caplog.text
    assert parsed.by_cnpj(fca.PETROBRAS).legal_name == "PETROLEO BRASILEIRO S.A. PETROBRAS"


def test_merging_years_lets_the_newer_filing_win() -> None:
    older = parse_package(
        fca.build_package(
            year=2025,
            general=[dict(fca.GENERAL_ROWS[0]) | {"Nome_Empresarial": "PETROBRAS, AS OF 2025"}],
            securities=[],
        )
    )
    newer = parse_package(fca.build_package(general=[fca.GENERAL_ROWS[0]], securities=[]))

    merged = merge_registries(older, newer)

    assert len(merged) == 1
    record = merged.by_cnpj(fca.PETROBRAS)
    assert record is not None and record.legal_name == "PETROLEO BRASILEIRO S.A. PETROBRAS"


def test_merging_keeps_companies_that_only_filed_in_the_older_year() -> None:
    older = parse_package(fca.build_package(year=2025))
    newer = parse_package(fca.build_package(general=[fca.GENERAL_ROWS[0]], securities=[]))

    merged = merge_registries(older, newer)

    assert len(merged) == len(fca.GENERAL_ROWS)
