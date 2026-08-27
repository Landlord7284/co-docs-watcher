"""The transport: exact payload, envelope translation, and the discipline the backend needs."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import httpx
import pytest

from co_docs_watcher.errors import (
    CaptchaRequiredError,
    DocumentError,
    RequestBudgetExceededError,
    SourceContractError,
    TransientSourceError,
)
from co_docs_watcher.rad.client import RadClient, search_payload
from co_docs_watcher.rad.schema import parse_listing
from tests import rad

DAY = date(2026, 8, 21)

Handler = Callable[[httpx.Request], httpx.Response]


class FakeTime:
    """A clock the tests own: ``sleep`` records its argument and advances ``monotonic``."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def make_client(handler: Handler, **kwargs: object) -> tuple[RadClient, FakeTime]:
    clock = FakeTime()
    options: dict[str, object] = {
        "min_request_interval": 0.0,
        "retries": 2,
        "backoff_initial": 1.0,
        "backoff_factor": 2.0,
    }
    options.update(kwargs)
    client = RadClient(
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        **options,  # type: ignore[arg-type]
    )
    return client, clock


def answering(response: httpx.Response) -> tuple[Handler, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return handler, seen


# --- The search payload. ---


def test_the_payload_is_built_exactly_as_the_front_does() -> None:
    assert search_payload(date(2026, 8, 4), ["009512", "004170"]) == {
        "dataDe": "04/08/2026",
        "dataAte": "04/08/2026",
        "empresa": ",009512,004170",
        "setorAtividade": "-1",
        "categoriaEmissor": "-1",
        "situacaoEmissor": "-1",
        "tipoParticipante": "-1",
        "dataReferencia": "",
        "categoria": "EST_-1,IPE_-1_-1_-1",
        "periodo": "2",
        "horaIni": "",
        "horaFim": "",
        "palavraChave": "",
        "ultimaDtRef": "false",
        "tipoEmpresa": "0",
        "token": "",
        "versaoCaptcha": "",
    }


def test_an_empty_company_list_means_the_whole_market() -> None:
    assert search_payload(DAY)["empresa"] == ""


def test_the_client_posts_the_payload_to_the_page_method() -> None:
    handler, seen = answering(httpx.Response(200, json=rad.envelope("linha$&&*")))
    client, _ = make_client(handler)

    dados = client.list_documents(DAY, ["009512"])

    assert dados == "linha$&&*"
    request = seen[0]
    assert str(request.url).endswith("frmConsultaExternaCVM.aspx/ListarDocumentos")
    assert request.headers["content-type"].startswith("application/json")
    assert json.loads(request.content) == search_payload(DAY, ["009512"])


# --- The envelope. ---


def test_tem_erro_is_a_transient_error_and_never_an_empty_result() -> None:
    handler, seen = answering(
        httpx.Response(200, json=rad.envelope(tem_erro=True, msg_erro="The HTTP service fell"))
    )
    client, clock = make_client(handler)

    with pytest.raises(TransientSourceError, match="The HTTP service fell"):
        client.list_documents(DAY)

    assert len(seen) == 3  # 1 + 2 retries: retried, then loud — never "nothing new"
    assert clock.sleeps == [1.0, 2.0]  # exponential backoff between attempts


def test_a_transient_failure_is_retried_until_the_source_recovers() -> None:
    answers = [
        httpx.Response(200, json=rad.envelope(tem_erro=True, msg_erro="down")),
        httpx.Response(200, json=rad.envelope("linha$&&*")),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return answers.pop(0)

    client, _ = make_client(handler)

    assert client.list_documents(DAY) == "linha$&&*"
    assert client.requests_made == 2


def test_no_retries_means_one_attempt_and_a_negative_count_is_refused() -> None:
    down = rad.envelope(tem_erro=True, msg_erro="down")
    handler, seen = answering(httpx.Response(200, json=down))
    client, clock = make_client(handler, retries=0)

    with pytest.raises(TransientSourceError):
        client.list_documents(DAY)

    assert len(seen) == 1
    assert clock.sleeps == []
    # Every other number here degrades into something with a meaning; this one degrades into
    # no attempt at all, so it is refused where it is written rather than where it lands.
    with pytest.raises(ValueError, match="retries cannot be negative"):
        make_client(handler, retries=-1)


def test_a_captcha_demand_short_circuits_with_no_retry_and_no_backoff() -> None:
    handler, seen = answering(httpx.Response(200, json=rad.envelope(captcha="S")))
    client, clock = make_client(handler)

    with pytest.raises(CaptchaRequiredError):
        client.list_documents(DAY)

    assert len(seen) == 1
    assert clock.sleeps == []


def test_the_captcha_wins_over_tem_erro_when_both_arrive() -> None:
    handler, _ = answering(
        httpx.Response(200, json=rad.envelope(tem_erro=True, msg_erro="down", captcha="S"))
    )
    client, _ = make_client(handler)

    with pytest.raises(CaptchaRequiredError):
        client.list_documents(DAY)


@pytest.mark.parametrize("answer", ["s", "Y", "", True, 1, ["S"]])
def test_a_captcha_answer_outside_the_vocabulary_is_contract_divergence(answer: object) -> None:
    # The one signal whose wrong reaction is to carry on requesting: an unrecognized
    # spelling must be loud, never read as "no captcha was demanded".
    envelope = rad.envelope("linha$&&*")
    envelope["d"]["SolicitarCaptcha"] = answer
    handler, seen = answering(httpx.Response(200, json=envelope))
    client, _ = make_client(handler)

    with pytest.raises(SourceContractError, match="SolicitarCaptcha"):
        client.list_documents(DAY)

    assert len(seen) == 1


def test_the_negative_answer_is_a_listing_like_any_other() -> None:
    handler, _ = answering(httpx.Response(200, json=rad.envelope("linha$&&*", captcha="N")))
    client, _ = make_client(handler)

    assert client.list_documents(DAY) == "linha$&&*"


@pytest.mark.parametrize(
    "mangle",
    [
        lambda envelope: envelope["d"].pop("dados"),
        lambda envelope: envelope["d"].pop("SolicitarCaptcha"),
        lambda envelope: envelope.pop("d"),
        lambda envelope: envelope["d"].__setitem__("dados", 7),
    ],
)
def test_envelope_divergence_is_a_contract_error(mangle: Callable[[dict], object]) -> None:
    envelope = rad.envelope("linha$&&*")
    mangle(envelope)
    handler, seen = answering(httpx.Response(200, json=envelope))
    client, _ = make_client(handler)

    with pytest.raises(SourceContractError):
        client.list_documents(DAY)

    assert len(seen) == 1  # divergence does not get better with insistence


def test_a_body_that_is_not_json_is_a_contract_error() -> None:
    handler, _ = answering(httpx.Response(200, content=b"<html>maintenance</html>"))
    client, _ = make_client(handler)

    with pytest.raises(SourceContractError, match="not JSON"):
        client.list_documents(DAY)


def test_a_5xx_is_transient_and_a_404_is_contract_divergence() -> None:
    handler, seen = answering(httpx.Response(502))
    client, _ = make_client(handler)
    with pytest.raises(TransientSourceError):
        client.list_documents(DAY)
    assert len(seen) == 3

    handler, seen = answering(httpx.Response(404))
    client, _ = make_client(handler)
    with pytest.raises(SourceContractError):
        client.list_documents(DAY)
    assert len(seen) == 1


def test_a_connection_failure_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client, _ = make_client(handler)

    with pytest.raises(TransientSourceError, match="refused"):
        client.list_documents(DAY)
    assert client.requests_made == 3


def test_a_listing_response_over_the_cap_is_a_contract_error() -> None:
    handler, _ = answering(httpx.Response(200, json=rad.envelope("x" * 4096)))
    client, _ = make_client(handler, max_listing_bytes=100)

    with pytest.raises(SourceContractError, match="byte cap"):
        client.list_documents(DAY)


# --- Rate discipline. ---


def test_the_minimum_interval_separates_consecutive_requests() -> None:
    handler, _ = answering(httpx.Response(200, json=rad.envelope()))
    client, clock = make_client(handler, min_request_interval=5.0, retries=0)

    client.list_documents(DAY)
    client.list_documents(DAY)

    assert clock.sleeps == [5.0]  # none before the first, the floor before the second


def test_listing_and_download_share_the_same_interval() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=rad.envelope())
        return httpx.Response(200, content=b"%PDF-1.6 ...")

    client, clock = make_client(handler, min_request_interval=5.0, retries=0)

    client.list_documents(DAY)
    client.fetch_document(1084789, 1, "1560083")

    assert clock.sleeps == [5.0]


def test_the_request_cap_is_a_hard_fuse() -> None:
    handler, _ = answering(httpx.Response(200, json=rad.envelope()))
    client, _ = make_client(handler, max_requests_per_run=2)

    client.list_documents(DAY)
    client.list_documents(DAY)
    with pytest.raises(RequestBudgetExceededError):
        client.list_documents(DAY)

    assert client.requests_made == 2


def test_retries_spend_the_same_budget_as_requests() -> None:
    handler, _ = answering(httpx.Response(200, json=rad.envelope(tem_erro=True, msg_erro="down")))
    client, _ = make_client(handler, max_requests_per_run=2, retries=5)

    with pytest.raises(RequestBudgetExceededError):
        client.list_documents(DAY)

    assert client.requests_made == 2


# --- Download. ---


def test_the_download_get_carries_the_persisted_arguments_and_an_empty_desc_tipo() -> None:
    handler, seen = answering(
        httpx.Response(
            200,
            content=b"%PDF-1.6 ...",
            headers={"content-disposition": "attachment; filename=009512000101011.pdf"},
        )
    )
    client, _ = make_client(handler)

    content = client.fetch_document(160125, 7, "009512FRE202620260700160125-70")

    # The body, and only the body: the headers the answer came with do not travel with it.
    assert content == b"%PDF-1.6 ..."
    request = seen[0]
    assert request.url.path.endswith("frmDownloadDocumento.aspx")
    assert dict(request.url.params) == {
        "Tela": "ext",
        "numSequencia": "160125",
        "numVersao": "7",
        "numProtocolo": "009512FRE202620260700160125-70",
        "descTipo": "",
        "CodigoInstituicao": "1",
    }


def test_a_download_over_the_cap_is_scoped_to_the_document() -> None:
    handler, _ = answering(httpx.Response(200, content=b"x" * 4096))
    client, _ = make_client(handler, max_download_bytes=100)

    with pytest.raises(DocumentError, match="byte cap"):
        client.fetch_document(160125, 7, "protocol")


def test_a_download_5xx_is_retried_like_any_transient_failure() -> None:
    answers = [httpx.Response(503), httpx.Response(200, content=b"%PDF-1.6")]

    def handler(request: httpx.Request) -> httpx.Response:
        return answers.pop(0)

    client, _ = make_client(handler)

    assert client.fetch_document(160125, 7, "protocol") == b"%PDF-1.6"


# --- Contract: a recorded envelope round-trips into documents. ---


def test_a_recorded_envelope_round_trips_through_client_and_schema() -> None:
    dados = rad.payload(*rad.RECORDED_ROWS)
    handler, _ = answering(httpx.Response(200, json=rad.envelope(dados)))
    client, _ = make_client(handler)

    documents = parse_listing(client.list_documents(DAY))

    assert [d.document_id for d in documents] == [1084789, 161032, 1084804, 1084782]
