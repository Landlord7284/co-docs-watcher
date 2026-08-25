"""A recorded FCA package, trimmed to the companies the tests reason about.

Column names and values are copied from the real ``fca_cia_aberta_2026.zip`` as published on
2026-08-24; only the number of rows is reduced. Rows are written as dicts of the columns that
carry meaning, and every published column is emitted — an empty string when the fixture does
not care — so that a column disappearing from the real file still fails the parser tests.

Three of the rows are worth naming, because they are the reason the resolver has fallbacks:
PLASCAR fills ``Codigo_Negociacao`` with ``B3``, TEGMA fills it in lower case, and SCHLOSSER
trades under two equally short roots.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Mapping, Sequence

ENCODING = "iso-8859-1"
DELIMITER = ";"

GENERAL_COLUMNS = (
    "CNPJ_Companhia",
    "Data_Referencia",
    "Versao",
    "ID_Documento",
    "Nome_Empresarial",
    "Data_Nome_Empresarial",
    "Nome_Empresarial_Anterior",
    "Data_Constituicao",
    "Codigo_CVM",
    "Data_Registro_CVM",
    "Categoria_Registro_CVM",
    "Data_Categoria_Registro_CVM",
    "Situacao_Registro_CVM",
    "Data_Situacao_Registro_CVM",
    "Pais_Origem",
    "Pais_Custodia_Valores_Mobiliarios",
    "Setor_Atividade",
    "Descricao_Atividade",
    "Situacao_Emissor",
    "Data_Situacao_Emissor",
    "Especie_Controle_Acionario",
    "Data_Especie_Controle_Acionario",
    "Dia_Encerramento_Exercicio_Social",
    "Mes_Encerramento_Exercicio_Social",
    "Data_Alteracao_Exercicio_Social",
    "Pagina_Web",
)

SECURITIES_COLUMNS = (
    "CNPJ_Companhia",
    "Data_Referencia",
    "Versao",
    "ID_Documento",
    "Nome_Empresarial",
    "Valor_Mobiliario",
    "Sigla_Classe_Acao_Preferencial",
    "Classe_Acao_Preferencial",
    "Codigo_Negociacao",
    "Composicao_BDR_Unit",
    "Mercado",
    "Sigla_Entidade_Administradora",
    "Entidade_Administradora",
    "Data_Inicio_Negociacao",
    "Data_Fim_Negociacao",
    "Segmento",
    "Data_Inicio_Listagem",
    "Data_Fim_Listagem",
)

PETROBRAS = "33.000.167/0001-01"
VALE = "33.592.510/0001-54"
ENERGISA = "00.864.214/0001-06"
TEGMA = "02.351.144/0001-18"
PLASCAR = "51.928.174/0001-50"
SCHLOSSER = "82.981.929/0001-03"

GENERAL_ROWS: tuple[Mapping[str, str], ...] = (
    {
        "CNPJ_Companhia": PETROBRAS,
        "Versao": "1",
        "ID_Documento": "156276",
        "Nome_Empresarial": "PETROLEO BRASILEIRO S.A. PETROBRAS",
        "Codigo_CVM": "009512",
        "Situacao_Registro_CVM": "Ativo",
    },
    {
        "CNPJ_Companhia": VALE,
        "Versao": "1",
        "ID_Documento": "156281",
        "Nome_Empresarial": "VALE S.A.",
        "Nome_Empresarial_Anterior": "Companhia Vale do Rio Doce",
        "Codigo_CVM": "004170",
        "Situacao_Registro_CVM": "Ativo",
    },
    {
        "CNPJ_Companhia": ENERGISA,
        "Versao": "1",
        "ID_Documento": "158184",
        "Nome_Empresarial": "ENERGISA S.A.",
        "Nome_Empresarial_Anterior": "SIDEPAR PARTICIPAÇÕES S.A.",
        "Codigo_CVM": "015253",
        "Situacao_Registro_CVM": "Ativo",
    },
    {
        "CNPJ_Companhia": TEGMA,
        "Versao": "1",
        "ID_Documento": "156431",
        "Nome_Empresarial": "TEGMA GESTAO LOGISTICA S.A.",
        "Nome_Empresarial_Anterior": "TEGMA GESTÃO LOGÍSTICA LTDA",
        "Codigo_CVM": "020800",
        "Situacao_Registro_CVM": "Ativo",
    },
    {
        "CNPJ_Companhia": PLASCAR,
        "Versao": "1",
        "ID_Documento": "156010",
        "Nome_Empresarial": "PLASCAR PARTICIPACOES INDUSTRIAIS S.A.",
        "Codigo_CVM": "013471",
        "Situacao_Registro_CVM": "Ativo",
    },
    {
        "CNPJ_Companhia": SCHLOSSER,
        "Versao": "1",
        "ID_Documento": "154620",
        "Nome_Empresarial": "CIA INDUSTRIAL SCHLOSSER S.A.",
        "Nome_Empresarial_Anterior": "G. SCHLOSSER & FILHOS",
        "Codigo_CVM": "003549",
        "Situacao_Registro_CVM": "Ativo",
    },
)

SECURITIES_ROWS: tuple[Mapping[str, str], ...] = (
    {
        "CNPJ_Companhia": PETROBRAS,
        "ID_Documento": "156276",
        "Valor_Mobiliario": "Ações Ordinárias",
        "Codigo_Negociacao": "PETR3",
    },
    {
        "CNPJ_Companhia": PETROBRAS,
        "ID_Documento": "156276",
        "Valor_Mobiliario": "Ações Preferenciais",
        "Codigo_Negociacao": "PETR4",
    },
    {
        "CNPJ_Companhia": PETROBRAS,
        "ID_Documento": "156276",
        "Valor_Mobiliario": "Debêntures",
        "Codigo_Negociacao": "",
    },
    {
        "CNPJ_Companhia": VALE,
        "ID_Documento": "156281",
        "Valor_Mobiliario": "Ações Ordinárias",
        "Codigo_Negociacao": "VALE3",
    },
    {
        "CNPJ_Companhia": ENERGISA,
        "ID_Documento": "158184",
        "Valor_Mobiliario": "Ações Ordinárias",
        "Codigo_Negociacao": "ENGI3",
    },
    {
        "CNPJ_Companhia": ENERGISA,
        "ID_Documento": "158184",
        "Valor_Mobiliario": "Ações Preferenciais",
        "Codigo_Negociacao": "ENGI4",
    },
    {
        "CNPJ_Companhia": ENERGISA,
        "ID_Documento": "158184",
        "Valor_Mobiliario": "Units",
        "Codigo_Negociacao": "ENGI11",
    },
    {
        "CNPJ_Companhia": ENERGISA,
        "ID_Documento": "158184",
        "Valor_Mobiliario": "Nota Comercial",
        "Codigo_Negociacao": "ENGI13",
        "Data_Fim_Negociacao": "2021-05-04",
    },
    {
        "CNPJ_Companhia": TEGMA,
        "ID_Documento": "156431",
        "Valor_Mobiliario": "Ações Ordinárias",
        "Codigo_Negociacao": "tgma3",
    },
    {
        "CNPJ_Companhia": PLASCAR,
        "ID_Documento": "156010",
        "Valor_Mobiliario": "Ações Ordinárias",
        "Codigo_Negociacao": "B3",
    },
    {
        "CNPJ_Companhia": SCHLOSSER,
        "ID_Documento": "154620",
        "Valor_Mobiliario": "Ações Ordinárias",
        "Codigo_Negociacao": "SC303",
    },
    {
        "CNPJ_Companhia": SCHLOSSER,
        "ID_Documento": "154620",
        "Valor_Mobiliario": "Ações Preferenciais",
        "Codigo_Negociacao": "SCL04",
    },
)


def csv_member(columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> bytes:
    """Render rows as the CVM publishes them: ISO-8859-1, semicolons, every column present."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(columns),
        delimiter=DELIMITER,
        restval="",
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(dict(row))
    return buffer.getvalue().encode(ENCODING)


def build_package(
    *,
    year: int = 2026,
    general: Sequence[Mapping[str, str]] = GENERAL_ROWS,
    securities: Sequence[Mapping[str, str]] = SECURITIES_ROWS,
    general_columns: Sequence[str] = GENERAL_COLUMNS,
    members: Mapping[str, bytes] | None = None,
) -> bytes:
    """Build a yearly package. ``members`` replaces the whole archive, for the broken cases."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if members is not None:
            for name, payload in members.items():
                archive.writestr(name, payload)
        else:
            archive.writestr(
                f"fca_cia_aberta_geral_{year}.csv", csv_member(general_columns, general)
            )
            archive.writestr(
                f"fca_cia_aberta_valor_mobiliario_{year}.csv",
                csv_member(SECURITIES_COLUMNS, securities),
            )
            archive.writestr(f"fca_cia_aberta_auditor_{year}.csv", csv_member(("CNPJ_CIA",), ()))
    return buffer.getvalue()
