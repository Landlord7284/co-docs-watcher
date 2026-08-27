"""Registry records, and the parser that builds them from an FCA package.

The FCA (*Formulário Cadastral*) is the annual registration form every listed company files,
published by the CVM as open data: one ZIP per year, several CSVs inside, semicolon-separated
and encoded in ISO-8859-1. Two members matter here — the general one, which carries the
identity fields, and the securities one, which carries the trading codes.

Two properties of the file shape the parser and both were measured against the 2026 package on
2026-08-24: **the general member holds one row per company already reduced to its latest
version** (675 rows, 675 CNPJs, 675 CVM codes), and **``CNPJ`` to ``CD_CVM`` is strictly
1:1**. Neither is promised by the publisher, so the parser re-derives the latest version
itself and treats a violation of the 1:1 relation as a probable contract change: logged
loudly, never quietly resolved into whichever row happened to come first.

Trading codes are joined on the *selected* version's ``ID_Documento``, which is what keeps a
superseded version's delisted tickers out of the record.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from co_docs_watcher.errors import RegistryError
from co_docs_watcher.text import normalize_cnpj, normalize_cvm_code

__all__ = [
    "Registry",
    "RegistryRecord",
    "merge_registries",
    "parse_package",
]

logger = logging.getLogger(__name__)

#: Members of the yearly package this parser reads. The year is part of every member name.
GENERAL_MEMBER = re.compile(r"^fca_cia_aberta_geral_\d{4}\.csv$")
SECURITIES_MEMBER = re.compile(r"^fca_cia_aberta_valor_mobiliario_\d{4}\.csv$")

#: The CVM publishes its open data in ISO-8859-1, not UTF-8, with ``;`` as the delimiter.
PACKAGE_ENCODING = "iso-8859-1"
PACKAGE_DELIMITER = ";"

#: A single CSV member is a few hundred kilobytes; the cap is three orders of magnitude above
#: that, and exists so a decompression bomb cannot be read into memory.
MAX_MEMBER_BYTES = 256 * 1024 * 1024

_GENERAL_COLUMNS = frozenset(
    {
        "CNPJ_Companhia",
        "Versao",
        "ID_Documento",
        "Nome_Empresarial",
        "Nome_Empresarial_Anterior",
        "Codigo_CVM",
        "Situacao_Registro_CVM",
    }
)
_SECURITIES_COLUMNS = frozenset(
    {"CNPJ_Companhia", "ID_Documento", "Codigo_Negociacao", "Data_Fim_Negociacao"}
)


@dataclass(frozen=True, slots=True)
class RegistryRecord:
    """One company as the registry describes it.

    ``previous_legal_name`` is not decoration: roughly three quarters of the 2026 records carry
    one (495 of 675, measured 2026-08-24), and a human searching for a company by the name they
    remember is searching for the previous one more often than not.

    ``trading_codes`` holds only codes still being traded, normalized to upper case and
    otherwise untouched — the field is free text at the source and validating it is the
    resolver's job, not the parser's.

    ``registration_status`` keeps the registry's own vocabulary (``Ativo``, ``Suspenso``,
    ``Cancelado``) verbatim: it is data, not a term of this system.
    """

    cvm_code: str
    cnpj: str
    legal_name: str
    previous_legal_name: str | None
    trading_codes: tuple[str, ...]
    registration_status: str


class Registry:
    """A set of registry records, indexed by the two identifiers that are unique.

    Construction is where the 1:1 relation is enforced, and in both directions: a CVM code
    claimed by two different CNPJs and a CNPJ claiming two different CVM codes are the same
    contract change seen from either end, and both are logged ``CRITICAL``. The colliding
    record is left out of the index it collides in — and out of that one only, since a
    collision on the code says nothing about the CNPJ — and kept in ``records``, so the
    anomaly is visible instead of averaged away.
    """

    __slots__ = ("_by_cnpj", "_by_cvm_code", "_records")

    def __init__(self, records: Iterable[RegistryRecord]) -> None:
        self._records = tuple(records)
        self._by_cvm_code: dict[str, RegistryRecord] = {}
        self._by_cnpj: dict[str, RegistryRecord] = {}
        for record in self._records:
            by_cnpj = self._by_cnpj.get(record.cnpj)
            if by_cnpj is not None and by_cnpj.cvm_code != record.cvm_code:
                logger.critical(
                    "registry: CNPJ %s claims two CVM codes (%s %r and %s %r); the CNPJ to "
                    "code relation was 1:1 when last measured, so this is probably a contract "
                    "change",
                    record.cnpj,
                    by_cnpj.cvm_code,
                    by_cnpj.legal_name,
                    record.cvm_code,
                    record.legal_name,
                )
            else:
                self._by_cnpj[record.cnpj] = record

            by_code = self._by_cvm_code.get(record.cvm_code)
            if by_code is not None and by_code.cnpj != record.cnpj:
                logger.critical(
                    "registry: CVM code %s is claimed by two CNPJs (%s %r and %s %r); the "
                    "code to CNPJ relation was 1:1 when last measured, so this is probably a "
                    "contract change",
                    record.cvm_code,
                    by_code.cnpj,
                    by_code.legal_name,
                    record.cnpj,
                    record.legal_name,
                )
                continue
            self._by_cvm_code[record.cvm_code] = record

    @property
    def records(self) -> tuple[RegistryRecord, ...]:
        return self._records

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[RegistryRecord]:
        return iter(self._records)

    def by_cvm_code(self, cvm_code: str) -> RegistryRecord | None:
        return self._by_cvm_code.get(normalize_cvm_code(cvm_code))

    def by_cnpj(self, cnpj: str) -> RegistryRecord | None:
        return self._by_cnpj.get(normalize_cnpj(cnpj))


def merge_registries(*registries: Registry) -> Registry:
    """Merge registries, later ones winning per company.

    The yearly package only contains companies that filed *that* year, so a run in February
    would see a few dozen of them. Reading the previous year and letting the current year
    override it is what makes the registry usable in January and still current in December.
    """
    merged: dict[str, RegistryRecord] = {}
    for registry in registries:
        for record in registry:
            merged[record.cnpj] = record
    return Registry(merged.values())


def parse_package(payload: bytes) -> Registry:
    """Parse one yearly FCA package into a registry.

    Structural divergence — a payload that is not a ZIP, a missing member, a missing column —
    raises rather than yielding a thinner registry: an empty answer here is indistinguishable
    from a company that simply is not listed, and that mistake ends in a folder named after a
    zero-padded number for no reason.
    """
    if not payload.startswith(b"PK\x03\x04"):
        raise RegistryError("registry package is not a ZIP archive")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            general = _read_member(archive, GENERAL_MEMBER, "general", _GENERAL_COLUMNS)
            securities = _read_member(archive, SECURITIES_MEMBER, "securities", _SECURITIES_COLUMNS)
    except zipfile.BadZipFile as exc:
        raise RegistryError(f"registry package is corrupt: {exc}") from exc

    latest = _latest_version_per_company(general)
    codes = _active_trading_codes(securities)
    return Registry(_records(latest, codes))


def _records(
    latest: Mapping[str, Mapping[str, str]],
    codes: Mapping[tuple[str, str], tuple[str, ...]],
) -> Iterator[RegistryRecord]:
    for cnpj, row in sorted(latest.items()):
        cvm_code = normalize_cvm_code(row["Codigo_CVM"])
        if not cvm_code:
            # The sweep is filtered against this code, so a company without one cannot be
            # watched at all. Dropping it is right; dropping it in silence is not — this is
            # the same loss the parser refuses everywhere else, one row at a time.
            logger.warning(
                "registry: skipping %r (CNPJ %s), whose CVM code is unusable (%r)",
                row["Nome_Empresarial"].strip(),
                cnpj,
                row["Codigo_CVM"],
            )
            continue
        yield RegistryRecord(
            cvm_code=cvm_code,
            cnpj=cnpj,
            legal_name=row["Nome_Empresarial"].strip(),
            previous_legal_name=row["Nome_Empresarial_Anterior"].strip() or None,
            trading_codes=codes.get((cnpj, row["ID_Documento"].strip()), ()),
            registration_status=row["Situacao_Registro_CVM"].strip(),
        )


def _read_member(
    archive: zipfile.ZipFile,
    pattern: re.Pattern[str],
    role: str,
    required: frozenset[str],
) -> list[Mapping[str, str]]:
    """Read one member into rows, refusing anything the rest of the parser could not read.

    A row shorter than the header leaves ``csv.DictReader`` filling the columns it never
    reached with ``None``, and every value below here is a string the parser strips. The
    check belongs to this function rather than to the five places that would otherwise guard
    against it: this is where a structural divergence already becomes a ``RegistryError``,
    and a row that arrives half-written is a divergence like any other.
    """
    names = sorted(name for name in archive.namelist() if pattern.match(name))
    if not names:
        raise RegistryError(f"registry package has no {role} member matching {pattern.pattern}")
    if len(names) > 1:
        # The year is part of every member name, so more than one match means the package
        # carries two years at once. Picking one would silently join the general member of
        # one year to the securities member of the other, and the join is on ``ID_Documento``:
        # the answer would be a registry where no company trades anything.
        raise RegistryError(
            f"registry package has {len(names)} {role} members ({', '.join(names)}); one "
            "package holds one year, so the registry format has probably changed"
        )
    name = names[0]
    info = archive.getinfo(name)
    if info.file_size > MAX_MEMBER_BYTES:
        raise RegistryError(
            f"registry member {name} expands to {info.file_size} bytes, over the "
            f"{MAX_MEMBER_BYTES} byte cap"
        )
    with archive.open(name) as raw:
        # ``file_size`` is what the archive declares, and the reader stops there: a header
        # that understates the member truncates the read and fails its CRC on the way out,
        # which arrives above as a corrupt package. So the cap checked against the claim is
        # the cap, and the extra byte here only makes the boundary case unambiguous.
        text = raw.read(MAX_MEMBER_BYTES + 1).decode(PACKAGE_ENCODING)
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=PACKAGE_DELIMITER)
    missing = sorted(required - set(reader.fieldnames or ()))
    if missing:
        raise RegistryError(
            f"registry member {name} is missing column(s): {', '.join(missing)}; the registry "
            "format has probably changed"
        )
    rows: list[Mapping[str, str]] = []
    for row in reader:
        if not row.get("CNPJ_Companhia"):
            continue
        unfilled = sorted(column for column in required if not isinstance(row.get(column), str))
        if unfilled:
            raise RegistryError(
                f"registry member {name} has a row (line {reader.line_num}) shorter than its "
                f"header, with no value at all for column(s): {', '.join(unfilled)}; the "
                "registry format has probably changed"
            )
        rows.append(row)
    return rows


def _latest_version_per_company(rows: Sequence[Mapping[str, str]]) -> dict[str, Mapping[str, str]]:
    """Keep one row per CNPJ: the highest ``(Versao, ID_Documento)``.

    The published member already arrives reduced this way, which is precisely why the
    reduction is re-derived here — a promise nobody made is a promise nobody keeps.
    """
    latest: dict[str, Mapping[str, str]] = {}
    ranks: dict[str, tuple[int, int]] = {}
    for row in rows:
        cnpj = normalize_cnpj(row["CNPJ_Companhia"])
        if not cnpj:
            logger.warning(
                "registry: skipping a row with an unusable CNPJ %r", row["CNPJ_Companhia"]
            )
            continue
        rank = (
            _as_int(row["Versao"], "Versao", cnpj),
            _as_int(row["ID_Documento"], "ID_Documento", cnpj),
        )
        held = latest.get(cnpj)
        code = normalize_cvm_code(row["Codigo_CVM"])
        if held is not None and normalize_cvm_code(held["Codigo_CVM"]) != code:
            logger.critical(
                "registry: CNPJ %s carries two CVM codes across versions (%s and %s); the CNPJ "
                "to code relation was 1:1 when last measured, so this is probably a contract "
                "change",
                cnpj,
                normalize_cvm_code(held["Codigo_CVM"]),
                code,
            )
        if held is None or rank > ranks[cnpj]:
            latest[cnpj] = row
            ranks[cnpj] = rank
    return latest


def _active_trading_codes(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], tuple[str, ...]]:
    """Group still-traded codes by ``(CNPJ, ID_Documento)``.

    A filled ``Data_Fim_Negociacao`` is the source saying the code stopped trading (55 of 963
    rows in the 2026 package, measured 2026-08-24). Naming a folder after a ticker the company
    no longer trades under is the kind of error that only shows up months later.
    """
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        if row["Data_Fim_Negociacao"].strip():
            continue
        code = row["Codigo_Negociacao"].strip().upper()
        if not code:
            continue
        cnpj = normalize_cnpj(row["CNPJ_Companhia"])
        if not cnpj:
            continue
        key = (cnpj, row["ID_Documento"].strip())
        codes = grouped.setdefault(key, [])
        if code not in codes:
            codes.append(code)
    return {key: tuple(sorted(codes)) for key, codes in grouped.items()}


def _as_int(value: str, column: str, cnpj: str) -> int:
    """The column as an integer, or ``-1`` with a reason on the log.

    The two columns this reads order the versions of one company, so a value that is not a
    number ranks the row below every readable one — which is the safe answer, and exactly the
    kind of answer that must not be reached quietly: a company frozen on an old version
    because its newest one has an unreadable ``Versao`` looks like a company that stopped
    filing.
    """
    try:
        return int(value.strip())
    except ValueError:
        logger.warning(
            "registry: CNPJ %s has an unreadable %s (%r); the row is ranked below every "
            "readable version of the same company",
            cnpj,
            column,
            value,
        )
        return -1
