"""The live suite: re-measures the real source. Every number in this repository is a dated
measurement, not a permanent truth, and this is the instrument that re-takes them.

Run explicitly with ``pytest -m live``. Never part of the default selection or of CI.

Discipline holds even here — *especially* here, because this suite talks to the real, fragile
backend: at least five seconds between requests, a hard budget for the whole suite run
(:data:`MAX_REQUESTS`), no retries, and a captcha demand stops the suite immediately instead
of pressing on. The whole suite costs two listing requests, three downloads and the five
requests of one reading-copy chain — around eleven in all, which at the interval below is
some three minutes of steady traffic against a backend measured falling over after about a
dozen calls in a few minutes. That is close enough to the edge that running this suite is a
deliberate act and never a habit.

A failure in this file usually means the source moved, not that the code broke: the failure
messages say so, and the fix is to update ``docs/fonte-rad.md`` with the newly observed
behavior and its date — and then the code, if the divergence is real.

Last full successful measurement: 2026-08-27.
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from co_docs_watcher.models import SourceDocument, SourceStatus
from co_docs_watcher.rad.client import BASE_URL, RadClient, search_payload
from co_docs_watcher.rad.reading_pdf import has_reading_page, reading_pdf
from co_docs_watcher.rad.schema import parse_listing

pytestmark = pytest.mark.live

TIMEZONE = ZoneInfo("America/Sao_Paulo")

#: Seconds between any two requests this suite issues, transport-level pacing included.
MIN_INTERVAL = 15.0

#: The hard budget the client is given: the suite's 3 downloads and the 5 hops of one
#: reading-copy chain, with no slack for retries — a failed request is a finding, not
#: something to insist on. The two listing fixtures are sent outside the client, so they
#: assert on the envelope rather than on what it digested.
MAX_REQUESTS = 8 + 3

ROW_SEPARATOR = "$&&*"
FIELD_SEPARATOR = "$&"
#: Field 4's key is free text (the species itself); only fields 5 and 6 promise ``yyyymmdd``.
SPAN_ORDER = re.compile(r"<spanOrder>(.*?)</spanOrder>", re.DOTALL)
DATE_KEY = re.compile(r"^\d{8}$")

#: Category prefixes measured to arrive as structured ZIP packages. The complement promises
#: nothing: category does not determine the type — a Comunicado ao Mercado arrived as a ZIP
#: with an ``.ipe`` member on 2026-08-25 — so only the signature table below is contract.
STRUCTURED = ("ITR", "DFP", "FRE", "FCA")

#: The sniffing table: the only two signatures a download may carry.
MAGICS = (b"%PDF-", b"PK\x03\x04")

DIVERGED = (
    "the source has diverged from the recorded contract; update docs/fonte-rad.md with the "
    "newly observed behavior and its date"
)


def measured_day() -> date:
    """The most recent full business day: yesterday, or the Friday before a weekend."""
    day = datetime.now(TIMEZONE).date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


@pytest.fixture(scope="module")
def envelope() -> dict:
    """One raw ``ListarDocumentos`` exchange: no session, no cookies, no ``__VIEWSTATE``.

    Sent outside :class:`RadClient` on purpose, so the suite can assert on the envelope
    itself instead of on what the client already digested.
    """
    response = httpx.post(
        BASE_URL + "frmConsultaExternaCVM.aspx/ListarDocumentos",
        json=search_payload(measured_day()),
        headers={"Content-Type": "application/json; charset=UTF-8"},
        timeout=120.0,
    )
    time.sleep(MIN_INTERVAL)
    assert response.status_code == 200, (
        f"the search PageMethod answered HTTP {response.status_code}; {DIVERGED}"
    )
    parsed = json.loads(response.text)
    envelope = parsed.get("d")
    assert isinstance(envelope, dict), f"the answer carries no 'd' envelope; {DIVERGED}"

    if envelope.get("SolicitarCaptcha") == "S":
        pytest.exit(
            "the source demanded a captcha: the suite stops here. Do not re-run now — "
            "reduce frequency and try again later.",
            returncode=4,
        )
    assert envelope.get("temErro") is False, (
        f"the backend answered temErro ({envelope.get('msgErro')!r}): transient — re-run the "
        "suite later rather than immediately"
    )
    return envelope


@pytest.fixture(scope="module")
def rows(envelope: dict) -> list[str]:
    dados = envelope["dados"]
    parts = dados.split(ROW_SEPARATOR)
    assert parts[-1] == "", f"the trailing row separator is gone; {DIVERGED}"
    return parts[:-1]


@pytest.fixture(scope="module")
def documents(envelope: dict) -> list[SourceDocument]:
    return parse_listing(envelope["dados"])


@pytest.fixture(scope="module")
def client() -> RadClient:
    with RadClient(
        min_request_interval=MIN_INTERVAL, max_requests_per_run=MAX_REQUESTS, retries=0
    ) as client:
        yield client


def test_the_search_is_accepted_without_session_artifacts(envelope: dict) -> None:
    """Last measured 2026-08-25: a bare JSON POST, no cookie jar, answers the business path."""
    expected = {"temErro", "msgErro", "SolicitarCaptcha", "dados"}
    missing = expected - envelope.keys()
    assert not missing, f"the envelope lost {sorted(missing)}; {DIVERGED}"


def test_every_row_splits_into_exactly_12_fields(rows: list[str]) -> None:
    """Last measured 2026-08-25: 12 ``$&``-separated fields, no escaping, every row."""
    assert rows, f"a business day with zero rows is not a listing; {DIVERGED}"
    widths = {len(row.split(FIELD_SEPARATOR)) for row in rows}
    assert widths == {12}, f"rows split into {sorted(widths)} fields, not 12; {DIVERGED}"


def test_fields_4_to_6_carry_span_order_sort_keys(rows: list[str]) -> None:
    """Last measured 2026-08-25: ``<spanOrder>`` keys, ``yyyymmdd`` in the two date fields."""
    for row in rows:
        fields = row.split(FIELD_SEPARATOR)
        for index in (4, 5, 6):
            assert SPAN_ORDER.search(fields[index]), (
                f"field {index} lost its <spanOrder> key ({fields[index]!r}); {DIVERGED}"
            )
        for index in (5, 6):
            key = SPAN_ORDER.search(fields[index]).group(1)
            assert key == "" or DATE_KEY.match(key), (
                f"field {index} sort key {key!r} is not yyyymmdd; {DIVERGED}"
            )


def test_a_one_day_global_sweep_is_plausible_and_unpaginated(
    rows: list[str], documents: list[SourceDocument]
) -> None:
    """Last measured 2026-08-25: ~450 documents on a normal day, one response, no paging.

    The bounds are deliberately loose — holidays are thin, peak seasons are thick — and they
    exist to catch truncation (a suspiciously round, small answer) and explosion, not to pin
    the market's mood.
    """
    assert 20 <= len(rows) <= 10_000, (
        f"{len(rows)} rows for {measured_day()} is outside anything measured; {DIVERGED}"
    )
    assert len(documents) == len(rows)
    statuses = {document.status for document in documents}
    assert SourceStatus.ACTIVE in statuses, f"no active document market-wide; {DIVERGED}"


def test_a_download_matches_the_sniffing_table(
    documents: list[SourceDocument], client: RadClient
) -> None:
    """Last measured 2026-08-25: every download starts with ``%PDF-`` or ``PK\\x03\\x04``.

    Category promises nothing about which: the Fato Relevante measured 2026-08-24 was raw
    PDF, and a Comunicado ao Mercado on 2026-08-25 was a ZIP holding an ``.ipe`` member.
    The signature is the whole contract, and this asserts exactly that and no more.
    """
    document = _pick(documents, structured=None)
    content = client.fetch_document(document.document_id, document.version, document.protocol)
    assert content.startswith(MAGICS), (
        f"document {document.identity} ({document.category}) starts with "
        f"{content[:16]!r}, which matches nothing in the sniffing table; {DIVERGED}"
    )


def test_a_structured_package_is_stable_except_the_generated_pdf(
    documents: list[SourceDocument], client: RadClient
) -> None:
    """Last measured 2026-08-25: between two downloads of the same package, every member
    keeps its name and its bytes — except the on-demand reading PDF, whose name carries the
    generation instant. Not every package carries one (an FRE v8 arrived with only XMLs,
    2026-08-25); when absent, the whole container must repeat. This is why the hash is
    recorded per file with a stability marker, and never dedupes."""
    document = _pick(documents, structured=True)
    first = client.fetch_document(document.document_id, document.version, document.protocol)
    second = client.fetch_document(document.document_id, document.version, document.protocol)
    assert first.startswith(b"PK\x03\x04"), (
        f"document {document.identity} ({document.category}) does not start with the ZIP "
        f"magic (got {first[:16]!r}); {DIVERGED}"
    )

    generated = re.compile(r"^\d+_\d+_\d+\.pdf$", re.IGNORECASE)
    with (
        zipfile.ZipFile(io.BytesIO(first)) as one,
        zipfile.ZipFile(io.BytesIO(second)) as two,
    ):
        stable_one = {name for name in one.namelist() if not generated.match(name)}
        stable_two = {name for name in two.namelist() if not generated.match(name)}
        assert stable_one == stable_two, (
            f"the stable member sets differ between downloads of {document.identity}; "
            f"{DIVERGED}"
        )
        for name in sorted(stable_one):
            assert one.read(name) == two.read(name), (
                f"stable member {name!r} of {document.identity} changed between two "
                f"downloads; {DIVERGED}"
            )


def _pick(documents: list[SourceDocument], *, structured: bool | None) -> SourceDocument:
    """An active document, smallest id first for determinism.

    ``structured=True`` narrows to the category prefixes measured to be ZIP packages;
    ``None`` accepts anything active, because the sniffing table is category-blind.
    """
    candidates = sorted(
        (
            document
            for document in documents
            if document.status is SourceStatus.ACTIVE
            and (structured is None or document.category.upper().startswith(STRUCTURED))
        ),
        key=lambda document: document.identity,
    )
    if not candidates:
        pytest.skip(
            f"{measured_day()} delivered no active "
            f"{'structured document' if structured else 'document'}; re-run on another day"
        )
    return candidates[0]


@pytest.fixture(scope="module")
def recent_week() -> list[SourceDocument]:
    """Seven days of the whole market in one exchange, for the cross-day questions.

    A single day cannot answer where a superseded row lives: the answer is a comparison
    between the day a document was delivered and the day its replacement was. The range is
    sent outside :class:`RadClient` for the same reason the envelope fixture is — the suite
    asserts on the source, not on what the client digested — and it does not draw on the
    client's budget.
    """
    last = measured_day()
    first = last - timedelta(days=6)
    payload = search_payload(last)
    payload["dataDe"] = f"{first.day:02d}/{first.month:02d}/{first.year:04d}"
    payload["dataAte"] = f"{last.day:02d}/{last.month:02d}/{last.year:04d}"

    response = httpx.post(
        BASE_URL + "frmConsultaExternaCVM.aspx/ListarDocumentos",
        json=payload,
        headers={"Content-Type": "application/json; charset=UTF-8"},
        timeout=180.0,
    )
    time.sleep(MIN_INTERVAL)
    assert response.status_code == 200, (
        f"the search PageMethod answered HTTP {response.status_code}; {DIVERGED}"
    )
    envelope = json.loads(response.text)["d"]
    if envelope.get("SolicitarCaptcha") == "S":
        pytest.exit(
            "the source demanded a captcha: the suite stops here. Do not re-run now — "
            "reduce frequency and try again later.",
            returncode=4,
        )
    assert envelope.get("temErro") is False, (
        f"the backend answered temErro ({envelope.get('msgErro')!r}): transient — re-run the "
        "suite later rather than immediately"
    )
    return parse_listing(envelope["dados"])


def test_a_superseded_row_keeps_its_original_delivery_date(
    recent_week: list[SourceDocument],
) -> None:
    """Last measured 2026-08-25: an ``Inativo`` or ``Cancelado`` row stays in the listing of
    the day it was **originally delivered**, not the day it was superseded or withdrawn.

    This is what the daily full sweep buys beyond gap recovery. A narrow discovery window
    observes only its own days however often it runs, so a supersession of an older document
    is visible only by re-querying the day that document was delivered on. Measured over
    2026-08-18..2026-08-24: of 34 non-active rows, 12 had an active successor delivered on a
    later day — 7 at a gap of one day, 2 at two, 2 at three, 1 at four.

    If this ever fails the other way — every non-active row sharing its delivery date with
    its successor — the supersession would be arriving under the current day instead, the
    monitor would see it for free, and the sweep's role would shrink to gap recovery alone.
    """
    lineages: dict[tuple[str, str, object], list[SourceDocument]] = {}
    for document in recent_week:
        key = (document.cvm_code, document.category, document.reference_date)
        lineages.setdefault(key, []).append(document)

    flagged = [d for d in recent_week if d.status is not SourceStatus.ACTIVE]
    assert flagged, (
        f"no Inativo or Cancelado row in seven days of the whole market; {DIVERGED}"
    )

    cross_day = []
    for document in flagged:
        key = (document.cvm_code, document.category, document.reference_date)
        successors = [
            other
            for other in lineages[key]
            if other.status is SourceStatus.ACTIVE
            and other.delivery_date > document.delivery_date
        ]
        if successors:
            gap = (min(s.delivery_date for s in successors) - document.delivery_date).days
            cross_day.append((gap, document))

    if not cross_day:
        pytest.skip(
            f"{len(flagged)} non-active row(s) in the week, none with an active successor "
            "delivered on a later day: a week too quiet to settle the question. Re-run on "
            "another week rather than reading this as a contradiction."
        )

    for gap, document in cross_day:
        assert gap > 0, (
            f"document {document.identity} ({document.status}) was delivered on "
            f"{document.delivery_date} and its active successor no later; {DIVERGED}"
        )


def test_an_fre_container_carries_no_reading_copy(
    recent_week: list[SourceDocument], client: RadClient
) -> None:
    """Last measured 2026-08-28, on Metalúrgica Gerdau's v3 and SLC Agrícola's v4: an FRE
    package holds the structured form and ``FormularioCadastral.xml``, and nothing else.

    This is the fact the reading-copy chain exists for. If a generated PDF ever appears in
    here, the chain becomes five requests spent on something the download already delivered.
    """
    document = _an_fre(recent_week)
    content = client.fetch_document(document.document_id, document.version, document.protocol)
    assert content.startswith(b"PK\x03\x04"), (
        f"FRE {document.identity} did not arrive as a container (got {content[:16]!r}); "
        f"{DIVERGED}"
    )

    generated = re.compile(r"^\d+_\d+_\d+\.pdf$", re.IGNORECASE)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
    assert not [name for name in names if generated.match(name)], (
        f"the FRE container of {document.identity} now carries a generated reading PDF "
        f"({names}), so the chain is no longer what it is for; {DIVERGED}"
    )


def test_the_reading_copy_chain_answers_a_padded_buffer_holding_a_pdf(
    recent_week: list[SourceDocument], client: RadClient
) -> None:
    """Last measured 2026-08-28 on document 161120: five requests, ~38 s, and a fixed 16 MiB
    buffer whose PDF ends at ``%%EOF`` with NUL padding behind it.

    What is asserted is the shape and not the size: the buffer is the source's to change,
    and the trimming rule is what has to keep holding. ``reading_pdf`` refuses anything that
    is not a PDF, carries no end marker, or trails something other than padding, so reaching
    the assertions below is most of the measurement.
    """
    document = _an_fre(recent_week)
    content = reading_pdf(client, document)

    assert content.startswith(b"%PDF-"), f"the reading copy is not a PDF; {DIVERGED}"
    assert content.rstrip().endswith(b"%%EOF"), (
        f"the reading copy was trimmed to something that is not the end of a PDF; {DIVERGED}"
    )


def _an_fre(documents: list[SourceDocument]) -> SourceDocument:
    """An active FRE out of the week, smallest id first, or a skip.

    A skipped measurement is honest and a fabricated one is not: an FRE is filed when a
    company files it, and a week without one says nothing about the chain.
    """
    candidates = sorted(
        (
            document
            for document in documents
            if document.status is SourceStatus.ACTIVE and has_reading_page(document)
        ),
        key=lambda document: document.document_id,
    )
    if not candidates:
        pytest.skip("no active FRE was delivered in the measured week")
    return candidates[0]
