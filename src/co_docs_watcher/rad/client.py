"""Transport to the source: the search PageMethod, the download GET, and rate discipline.

There is no session, no cookie, and no ``__VIEWSTATE``: the search is a single JSON POST and
the download a single GET, verified to answer identically with no cookie at all. What the
download hands back is the body and nothing else: ``Content-Type`` says ``text/html`` for
PDFs and ZIPs alike and ``Content-Disposition`` names nothing a human or a machine can use,
so carrying either one further would only offer a later decision a header this source is
measured lying in. What the
transport owns instead is discipline, shared by both paths, because the WCF service behind
the page drops under load and stays down for about an hour: a minimum interval between any
two requests, a per-run request cap, and exponential backoff on transient failures.

**HTTP is always 200** on the business path. Failure arrives as ``temErro: true`` with text
in ``msgErro`` — a retryable :class:`TransientSourceError`, never an empty result: a robot
that reads a backend failure as "nothing new" records silence as good news.
``SolicitarCaptcha: "S"`` is the opposite of retryable: there is no legitimate workaround,
insisting aggravates the trigger, and the only remedy is reducing frequency — the error is
terminal and the run ends with exit code 4. It is read as a vocabulary of two, ``"S"`` and
``"N"``, and never as "S or not": a demand this build fails to recognize is answered by
insisting, which is the one reaction that makes the source's answer worse, so a third
spelling is contract divergence and says so.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
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
    "search_payload",
]

logger = logging.getLogger(__name__)

BASE_URL = "https://www.rad.cvm.gov.br/ENETWeb/"
_SEARCH_PATH = "frmConsultaExternaCVM.aspx/ListarDocumentos"
_DOWNLOAD_PATH = "frmDownloadDocumento.aspx"

#: Seconds between any two requests, search and download alike. The floor, not a target,
#: and the fallback only: the configuration file is where this is meant to be set. The
#: backend was measured falling over after about a dozen calls in a few minutes and staying
#: down for an hour, so the floor sits well outside that spacing — the threshold is unknown
#: and can only be found by provoking it, which costs an hour of the source each time.
DEFAULT_MIN_REQUEST_INTERVAL = 15.0

#: The safety fuse: one run never issues more requests than this.
DEFAULT_MAX_REQUESTS_PER_RUN = 200

#: What one answer may weigh, and the fallbacks only: the configuration file is where these
#: are meant to be set. A full market day measured ~500 KB for 479 documents and the largest
#: measured delivery is an 8.6 MB ITR package, so both sit far past any real answer — they
#: bound what a malfunction can spend rather than limiting how large a document may be, and
#: they are counted while the body streams in, so memory is bounded and not merely measured.
MAX_LISTING_BYTES = 64 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024

#: Backoff after a transient failure: 15 s, then 60 s, then 240 s, and the fallbacks only. The
#: backend was observed staying down for about an hour, so short retries would spend the
#: request budget without outliving the outage.
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_INITIAL = 15.0
DEFAULT_BACKOFF_FACTOR = 4.0

_TIMEOUT = httpx.Timeout(120.0, connect=30.0)

_T = TypeVar("_T")

#: The keys every answer must carry before it means anything.
_ENVELOPE_KEYS = frozenset({"temErro", "msgErro", "SolicitarCaptcha", "dados"})

#: The whole vocabulary of ``SolicitarCaptcha``. Checked against both spellings rather than
#: against ``"S"`` alone, because the reaction to an unrecognized demand would be to carry on
#: requesting. A tuple and not a set: the value is whatever JSON delivered, and membership in
#: a set would hash it — a list on the wire would then raise something nobody is catching.
_CAPTCHA_ANSWERS = ("S", "N")


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
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if retries < 0:
            # Every other number here degrades into something with a meaning — an interval
            # below zero is no wait, a budget of zero refuses the first request — and this
            # one degrades into no attempt at all, which is not a policy anybody could want.
            raise ValueError(f"retries cannot be negative, got {retries}")
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
        # Resolved at construction, not at definition: tests that stub ``time.sleep`` reach
        # a client built afterwards, even one built deep inside the composition root.
        self._sleep = time.sleep if sleep is None else sleep
        self._monotonic = time.monotonic if monotonic is None else monotonic
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

    def fetch_document(self, document_id: int, version: int, protocol: str) -> bytes:
        """One document's bytes, whatever they turn out to be, and nothing else.

        The protocol is a required argument, persisted at discovery — it cannot be derived,
        and without it a document discovered today could not be downloaded tomorrow.
        ``descTipo`` goes empty: measured working for every category. The response headers
        stop here: what the body is gets decided by the body.
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

    def _download(self, params: dict[str, str]) -> bytes:
        content, _ = self._request(
            "GET",
            self._base_url + _DOWNLOAD_PATH,
            cap=self._max_download_bytes,
            over_cap=DocumentError(
                f"the download exceeded the {self._max_download_bytes} byte cap"
            ),
            params=params,
        )
        return content

    # --- Discipline. ---

    def _with_retries(self, operation: Callable[[], _T], *, what: str) -> _T:
        """Retry ``operation`` on transient failures only, with exponential backoff.

        A captcha demand and contract divergence pass straight through: neither gets better
        with insistence, and the captcha in particular gets worse.
        """
        delay = self._backoff_initial
        for attempt in range(self._retries):
            try:
                return operation()
            except TransientSourceError as error:
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
        # The last attempt is the one nothing follows, so its failure is simply the caller's.
        # Written outside the loop rather than as a branch inside it: the alternative needs a
        # line after the loop that cannot be reached, and a line that cannot be reached is a
        # claim about the loop rather than a fact about it.
        return operation()

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
    captcha = envelope["SolicitarCaptcha"]
    if captcha not in _CAPTCHA_ANSWERS:
        raise SourceContractError(
            f"the envelope's SolicitarCaptcha is neither 'S' nor 'N': {captcha!r}"
        )
    if captcha == "S":
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
