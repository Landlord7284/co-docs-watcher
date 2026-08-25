"""Transport to the source: the search PageMethod, the download GET, and rate discipline.

There is no session, no cookie, and no ``__VIEWSTATE``: the search is a single JSON POST and
the download a single GET, verified to answer identically with no cookie at all. What the
transport owns instead is discipline, shared by both paths, because the WCF service behind
the page drops under load and stays down for about an hour: a minimum interval between any
two requests, a per-run request cap, and exponential backoff on transient failures.

**HTTP is always 200** on the business path. Failure arrives as ``temErro: true`` with text
in ``msgErro`` — a retryable :class:`TransientSourceError`, never an empty result: a robot
that reads a backend failure as "nothing new" records silence as good news.
``SolicitarCaptcha: "S"`` is the opposite of retryable: there is no legitimate workaround,
insisting aggravates the trigger, and the only remedy is reducing frequency — the error is
terminal and the run ends with exit code 4.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from types import TracebackType
from typing import Any, TypeVar

import httpx

from co_docs_watcher.errors import (
    CaptchaRequiredError,
    DocumentError,
    RequestBudgetExceededError,
    SourceContractError,
    TransientSourceError,
)
from co_docs_watcher.rad.vocabulary import ALL_DOCUMENTS

__all__ = [
    "BASE_URL",
    "DEFAULT_MAX_REQUESTS_PER_RUN",
    "DEFAULT_MIN_REQUEST_INTERVAL",
    "MAX_DOWNLOAD_BYTES",
    "MAX_LISTING_BYTES",
    "RadClient",
    "RawDownload",
    "search_payload",
]

logger = logging.getLogger(__name__)

BASE_URL = "https://www.rad.cvm.gov.br/ENETWeb/"
_SEARCH_PATH = "frmConsultaExternaCVM.aspx/ListarDocumentos"
_DOWNLOAD_PATH = "frmDownloadDocumento.aspx"

#: Seconds between any two requests, search and download alike. The floor, not a target.
DEFAULT_MIN_REQUEST_INTERVAL = 5.0

#: The safety fuse: one run never issues more requests than this.
DEFAULT_MAX_REQUESTS_PER_RUN = 200

#: A full market day measured ~500 KB for 479 documents; the cap is well past any real
#: listing and well short of a response that could exhaust memory.
MAX_LISTING_BYTES = 64 * 1024 * 1024

#: The largest measured delivery is an 8.6 MB ITR package; structured filings grow, but a
#: response past this cap is a source malfunction, not a document.
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024

#: Backoff after a transient failure: 15 s, then 60 s, then 240 s. The backend was observed
#: staying down for about an hour, so short retries would only spend the request budget.
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_INITIAL = 15.0
DEFAULT_BACKOFF_FACTOR = 4.0

_TIMEOUT = httpx.Timeout(120.0, connect=30.0)

_T = TypeVar("_T")

#: The keys every answer must carry before it means anything.
_ENVELOPE_KEYS = frozenset({"temErro", "msgErro", "SolicitarCaptcha", "dados"})


def search_payload(day: date, companies: Sequence[str] = ()) -> dict[str, str]:
    """The ``ListarDocumentos`` body, built exactly as the page's front end builds it.

    ``empresa`` is a comma-separated list of six-digit zero-padded CVM codes **with a
    leading comma** — an artifact of the front end's join that the server expects — and
    empty means the whole market. The dates are only read with ``periodo: "2"`` and filter
    by delivery date, both inclusive; discovery calls this with a single day.
    """
    wire_date = f"{day.day:02d}/{day.month:02d}/{day.year:04d}"
    return {
        "dataDe": wire_date,
        "dataAte": wire_date,
        "empresa": "".join(f",{code}" for code in companies),
        "setorAtividade": "-1",
        "categoriaEmissor": "-1",
        "situacaoEmissor": "-1",
        "tipoParticipante": "-1",
        "dataReferencia": "",
        "categoria": ALL_DOCUMENTS,
        "periodo": "2",
        "horaIni": "",
        "horaFim": "",
        "palavraChave": "",
        "ultimaDtRef": "false",
        "tipoEmpresa": "0",
        "token": "",
        "versaoCaptcha": "",
    }


@dataclass(frozen=True, slots=True)
class RawDownload:
    """One download response, still unexamined.

    ``content_disposition`` travels along only as the secondary hint the sniffing order
    allows it to be; it never names a file and never decides a type on its own.
    """

    content: bytes
    content_disposition: str


class RadClient:
    """The one transport both halves of the adapter share.

    Sharing is the point: the minimum interval and the request cap only mean something if
    listing and downloading draw from the same account.
    """

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        min_request_interval: float = DEFAULT_MIN_REQUEST_INTERVAL,
        max_requests_per_run: int = DEFAULT_MAX_REQUESTS_PER_RUN,
        retries: int = DEFAULT_RETRIES,
        backoff_initial: float = DEFAULT_BACKOFF_INITIAL,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        max_listing_bytes: int = MAX_LISTING_BYTES,
        max_download_bytes: int = MAX_DOWNLOAD_BYTES,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._min_interval = min_request_interval
        self._max_requests = max_requests_per_run
        self._retries = retries
        self._backoff_initial = backoff_initial
        self._backoff_factor = backoff_factor
        self._max_listing_bytes = max_listing_bytes
        self._max_download_bytes = max_download_bytes
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=_TIMEOUT)
        self._sleep = sleep
        self._monotonic = monotonic
        self._requests_made = 0
        self._last_request_at: float | None = None

    @property
    def requests_made(self) -> int:
        """Requests actually issued this run, retries included. What the fuse counts."""
        return self._requests_made

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> RadClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def list_documents(self, day: date, companies: Sequence[str] = ()) -> str:
        """One day's listing, whole market unless narrowed, as the raw ``dados`` string.

        Parsing belongs to ``schema.py``; this method owns the envelope and nothing past
        it. Retries transparently on transient failures, within the shared budget.
        """
        payload = search_payload(day, companies)
        return self._with_retries(
            lambda: self._search(payload), what=f"listing {day.isoformat()}"
        )

    def fetch_document(self, document_id: int, version: int, protocol: str) -> RawDownload:
        """One document's bytes, whatever they turn out to be.

        The protocol is a required argument, persisted at discovery — it cannot be derived,
        and without it a document discovered today could not be downloaded tomorrow.
        ``descTipo`` goes empty: measured working for every category.
        """
        params = {
            "Tela": "ext",
            "numSequencia": str(document_id),
            "numVersao": str(version),
            "numProtocolo": protocol,
            "descTipo": "",
            "CodigoInstituicao": "1",
        }
        return self._with_retries(
            lambda: self._download(params), what=f"download of ({document_id}, {version})"
        )

    # --- One attempt of each operation. ---

    def _search(self, payload: dict[str, str]) -> str:
        body, _ = self._request(
            "POST",
            self._base_url + _SEARCH_PATH,
            cap=self._max_listing_bytes,
            over_cap=SourceContractError(
                f"the listing response exceeded the {self._max_listing_bytes} byte cap"
            ),
            json=payload,
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        return _open_envelope(body)

    def _download(self, params: dict[str, str]) -> RawDownload:
        content, headers = self._request(
            "GET",
            self._base_url + _DOWNLOAD_PATH,
            cap=self._max_download_bytes,
            over_cap=DocumentError(
                f"the download exceeded the {self._max_download_bytes} byte cap"
            ),
            params=params,
        )
        return RawDownload(
            content=content,
            content_disposition=headers.get("content-disposition", ""),
        )

    # --- Discipline. ---

    def _with_retries(self, operation: Callable[[], _T], *, what: str) -> _T:
        """Retry ``operation`` on transient failures only, with exponential backoff.

        A captcha demand and contract divergence pass straight through: neither gets better
        with insistence, and the captcha in particular gets worse.
        """
        delay = self._backoff_initial
        for attempt in range(self._retries + 1):
            try:
                return operation()
            except TransientSourceError as error:
                if attempt == self._retries:
                    raise
                logger.warning(
                    "source: %s failed (%s); attempt %d of %d, backing off %.0f s",
                    what,
                    error,
                    attempt + 1,
                    self._retries + 1,
                    delay,
                )
                self._sleep(delay)
                delay *= self._backoff_factor
        raise AssertionError("unreachable")

    def _request(
        self,
        method: str,
        url: str,
        *,
        cap: int,
        over_cap: Exception,
        **kwargs: Any,
    ) -> tuple[bytes, httpx.Headers]:
        """One HTTP exchange, gated by the fuse and the minimum interval, read capped.

        The body is streamed so the cap bounds memory rather than merely measuring the
        damage after the fact.
        """
        if self._requests_made >= self._max_requests:
            raise RequestBudgetExceededError(
                f"the per-run cap of {self._max_requests} requests was reached; whatever is "
                "still pending waits for the next run"
            )
        if self._last_request_at is not None:
            elapsed = self._monotonic() - self._last_request_at
            wait = self._min_interval - elapsed
            if wait > 0:
                self._sleep(wait)
        self._requests_made += 1
        try:
            response = self._http.send(self._http.build_request(method, url, **kwargs), stream=True)
        except httpx.HTTPError as error:
            self._last_request_at = self._monotonic()
            raise TransientSourceError(f"{method} {url} failed: {error}") from error
        try:
            if response.status_code >= 500:
                raise TransientSourceError(
                    f"{method} {url} answered HTTP {response.status_code}"
                )
            if response.status_code != 200:
                # The business path always answers 200; anything else is a different
                # endpoint than the one this build knows.
                raise SourceContractError(
                    f"{method} {url} answered HTTP {response.status_code}"
                )
            chunks: list[bytes] = []
            size = 0
            try:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > cap:
                        raise over_cap
                    chunks.append(chunk)
            except httpx.HTTPError as error:
                raise TransientSourceError(f"{method} {url} broke mid-read: {error}") from error
            return b"".join(chunks), response.headers
        finally:
            response.close()
            self._last_request_at = self._monotonic()


def _open_envelope(body: bytes) -> str:
    """The JSON envelope around the ``dados`` string, checked in the order that matters.

    The captcha demand is checked before ``temErro``: if both arrive, retrying is exactly
    the wrong reaction.
    """
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise SourceContractError(f"the listing answer is not JSON: {error}") from error
    envelope = parsed.get("d") if isinstance(parsed, dict) else None
    if not isinstance(envelope, dict):
        raise SourceContractError("the listing answer carries no 'd' envelope")
    missing = sorted(_ENVELOPE_KEYS - envelope.keys())
    if missing:
        raise SourceContractError(f"the listing envelope is missing {', '.join(missing)}")
    if envelope["SolicitarCaptcha"] == "S":
        raise CaptchaRequiredError(
            "the source demanded a captcha; there is no legitimate workaround — reduce "
            "the run frequency"
        )
    if envelope["temErro"]:
        message = envelope["msgErro"] or "the source answered temErro with no message"
        raise TransientSourceError(str(message))
    dados = envelope["dados"]
    if not isinstance(dados, str):
        raise SourceContractError(f"the envelope's dados is not a string: {type(dados).__name__}")
    return dados
