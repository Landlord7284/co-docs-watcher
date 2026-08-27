"""Row parsing: the twelve ``$&``-separated fields become ``SourceDocument``.

The envelope is JSON; the content is not. Rows travel as a single string with literal,
unescaped separators — ``$&&*`` between rows, ``$&`` between fields — inherited from the
system this one replaced. Because nothing is escaped, a subject containing ``$&`` corrupts
its row silently; the only defense is to validate that every row has exactly twelve fields
and abort the whole collection on divergence. The system is recently migrated (live since
2026-07-06): a partially understood payload is worse than none.

Twelve is a count, not a shape. Eight of the twelve fields refuse a value that is not
theirs — a CVM code, two sort keys, a status, a version, a modality, a download call — and
the remaining four are prose, which no value is wrong for on its own. That asymmetry is
where a young wire format drifts unnoticed: a column redefined in place, a subject that
starts arriving as markup, an icons block copied into the field beside it, each of them
leaving twelve fields with eight of them still perfectly valid. What prose can be checked
for is the markers of the fields that are *not* prose — the ``<spanOrder>`` sort key of
fields 4 to 6 and the download call of field 10 — and either one where prose belongs aborts
the collection. An exact swap of two prose fields for each other stays invisible; nothing
structural can see it, and the archive would carry the category under the type's name
without a word. That is the residue of an unescaped wire format, recorded here rather than
argued away.

Fields 4 to 6 embed a normalized sort key in ``<spanOrder>`` tags. For the two dates it is
``yyyymmdd`` and is parsed instead of the display format; for the species (field 4) the key
carries the species text itself — free text, empty on structured documents (measured
2026-08-24). Field 10 is the action-icons HTML, and inside it the
``OpenDownloadDocumentos(numSequencia, numVersao, numProtocolo, descTipo)`` call carries the
download arguments. Every row has it — including ``Inativo`` and ``Cancelado`` rows
(measured 2026-08-24 over a full market day) — so a missing call is contract divergence,
not a quiet document.
"""

from __future__ import annotations

import re
from datetime import date

from co_docs_watcher.errors import SourceContractError
from co_docs_watcher.models import SourceDocument, SourceStatus
from co_docs_watcher.text import normalize_cvm_code

__all__ = [
    "FIELD_COUNT",
    "FIELD_SEPARATOR",
    "ROW_SEPARATOR",
    "parse_listing",
    "parse_row",
]

ROW_SEPARATOR = "$&&*"
FIELD_SEPARATOR = "$&"

#: Exactly this many fields per row, or the collection aborts.
FIELD_COUNT = 12

_STATUSES = {
    "Ativo": SourceStatus.ACTIVE,
    "Inativo": SourceStatus.INACTIVE,
    "Cancelado": SourceStatus.CANCELLED,
}

#: AP is a first presentation, RE a spontaneous resubmission, RC a resubmission demanded by
#: the regulator (observed live 2026-08-25, in the 2026-08-24 market listing). Anything else
#: is divergence.
_MODALITIES = frozenset({"AP", "RE", "RC"})

_SPAN_ORDER = re.compile(r"^\s*<spanOrder>(?P<key>.*?)</spanOrder>", re.DOTALL)

#: The page's own JavaScript quotes all four arguments (``'160125','7','…','ITR'``); the
#: pattern tolerates the unquoted spelling as well, since nothing about it is promised.
_DOWNLOAD_CALL = re.compile(
    r"OpenDownloadDocumentos\(\s*'?(?P<document_id>\d+)'?\s*,\s*'?(?P<version>\d+)'?\s*,"
    r"\s*'(?P<protocol>[^']+)'\s*,\s*'(?P<desc_tipo>[^']*)'\s*\)"
)

_SORT_KEY_DATE = re.compile(r"^(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})$")

#: What a prose field must never contain: the source's own markers for the fields that carry
#: structure. Quoted literally, as wire vocabulary is — and deliberately just these two, so
#: that the check refuses a displaced column and never a company writing ``<`` in a subject.
_STRUCTURAL_MARKERS = ("<spanOrder>", "OpenDownloadDocumentos(")


def parse_listing(payload: str) -> list[SourceDocument]:
    """Parse one day's ``dados`` string into documents, strictly.

    The payload ends with a row separator, so the split leaves a trailing empty element —
    discarded before counting, which is also what makes an empty payload an empty day
    rather than a parse error. Any malformed row aborts the whole collection.
    """
    rows = payload.split(ROW_SEPARATOR)
    if rows and rows[-1] == "":
        rows.pop()
    return [parse_row(row, index) for index, row in enumerate(rows)]


def parse_row(row: str, index: int = 0) -> SourceDocument:
    """Translate one wire row. ``index`` only sharpens the error messages."""
    fields = row.split(FIELD_SEPARATOR)
    if len(fields) != FIELD_COUNT:
        raise SourceContractError(
            f"listing row {index}: expected {FIELD_COUNT} fields, got {len(fields)}; the "
            "separators are unescaped, so a divergent count means the row cannot be trusted"
        )

    cvm_code = normalize_cvm_code(fields[0])
    if not cvm_code or len(cvm_code) != 6:
        raise SourceContractError(
            f"listing row {index}: field 0 is not a CVM code: {fields[0]!r}"
        )

    document_id, version, protocol = _download_arguments(fields[10], index)
    declared_version = _integer(fields[8], index, what="field 8 (version)")
    if version != declared_version:
        raise SourceContractError(
            f"listing row {index}: field 8 says version {declared_version} but the download "
            f"call says {version}; the row cannot be trusted"
        )

    modality = fields[9].strip()
    if modality not in _MODALITIES:
        raise SourceContractError(
            f"listing row {index}: field 9 is not a known modality: {fields[9]!r}"
        )

    status = _STATUSES.get(fields[7].strip())
    if status is None:
        raise SourceContractError(
            f"listing row {index}: field 7 is not a known status: {fields[7]!r}"
        )

    delivery_date = _sort_key_date(fields[6], index, what="field 6 (delivery date)")
    if delivery_date is None:
        raise SourceContractError(
            f"listing row {index}: field 6 carries no delivery date: {fields[6]!r}"
        )

    return SourceDocument(
        document_id=document_id,
        version=version,
        protocol=protocol,
        cvm_code=cvm_code,
        legal_name=_prose(fields[1], index, what="field 1 (legal name)"),
        category=_display(fields[2], index, what="field 2 (category)"),
        doc_type=_display(fields[3], index, what="field 3 (type)"),
        species=_span_order_key(fields[4], index, what="field 4 (species)"),
        subject=_prose(fields[11], index, what="field 11 (subject)"),
        modality=modality,
        status=status,
        delivery_date=delivery_date,
        reference_date=_sort_key_date(fields[5], index, what="field 5 (reference date)"),
    )


def _prose(field: str, index: int, *, what: str) -> str:
    """A field that carries prose, refused if it carries a structured field's marker.

    There is no value this field could hold that is wrong on its own — which is exactly why
    it is the one place a displaced column would land unnoticed.
    """
    for marker in _STRUCTURAL_MARKERS:
        if marker in field:
            raise SourceContractError(
                f"listing row {index}: {what} carries {marker!r}, which belongs to a "
                "structured field: the columns have moved and the row cannot be trusted"
            )
    return field.strip()


def _display(field: str, index: int, *, what: str) -> str:
    """Prose, with the page's ``-`` placeholder read as absence.

    Only the type and the species arrive spelled that way (measured 2026-08-24), so the
    reading is not extended to the name or the subject: a subject of ``-`` is a subject the
    company wrote, and turning it into silence would be inventing an absence.
    """
    stripped = _prose(field, index, what=what)
    return "" if stripped == "-" else stripped


def _span_order_key(field: str, index: int, *, what: str) -> str:
    """The content of the embedded ``<spanOrder>`` tag, required even when empty."""
    match = _SPAN_ORDER.match(field)
    if match is None:
        raise SourceContractError(
            f"listing row {index}: {what} carries no <spanOrder> sort key: {field!r}"
        )
    return match.group("key").strip()


def _sort_key_date(field: str, index: int, *, what: str) -> date | None:
    """The ``yyyymmdd`` sort key as a date, or ``None`` when the key is empty.

    The display format is never parsed: the reference date alone arrives in several
    spellings (``2026``, ``30/06/2026``), and the sort key exists precisely so that nobody
    has to interpret them.
    """
    key = _span_order_key(field, index, what=what)
    if not key:
        return None
    match = _SORT_KEY_DATE.match(key)
    if match is None:
        raise SourceContractError(
            f"listing row {index}: {what} sort key is not yyyymmdd: {key!r}"
        )
    try:
        return date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
    except ValueError as exc:
        raise SourceContractError(
            f"listing row {index}: {what} sort key is not a real date: {key!r}"
        ) from exc


def _integer(field: str, index: int, *, what: str) -> int:
    try:
        return int(field.strip())
    except ValueError as exc:
        raise SourceContractError(
            f"listing row {index}: {what} is not an integer: {field!r}"
        ) from exc


def _download_arguments(field: str, index: int) -> tuple[int, int, str]:
    """The three download arguments this system persists, out of the four in the call.

    ``descTipo`` is parsed and dropped: the measured download works with it empty for
    every category, and the neutral model deliberately carries nothing wire-specific.
    """
    match = _DOWNLOAD_CALL.search(field)
    if match is None:
        raise SourceContractError(
            f"listing row {index}: field 10 carries no OpenDownloadDocumentos(...) call; "
            "without it the document can never be downloaded"
        )
    return (
        int(match.group("document_id")),
        int(match.group("version")),
        match.group("protocol"),
    )
