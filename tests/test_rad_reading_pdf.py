"""The reading-copy chain: what the source says about itself, and what is read instead.

The generator reports the size of a fixed buffer and never of the document, and it has two
ways of demanding a captcha. Both are pinned here against the shapes measured 2026-08-28 on
document 161120.
"""

from __future__ import annotations

import base64
from datetime import date
from typing import Any

import pytest

from co_docs_watcher.errors import (
    CaptchaRequiredError,
    DocumentError,
    SourceContractError,
    TransientSourceError,
)
from co_docs_watcher.models import SourceDocument, SourceStatus
from co_docs_watcher.rad.reading_pdf import has_reading_page, reading_pdf
from tests.rad import (
    READING_BUFFER_BYTES,
    generator_control,
    padded_pdf,
    section_tree,
    viewer_page,
)

DOCUMENT_PDF = b"%PDF-1.4\nreading copy\n%%EOF"


def document(**overrides: object) -> SourceDocument:
    values: dict = {
        "document_id": 161120,
        "version": 3,
        "protocol": "008656FRE202620260300161120-72",
        "cvm_code": "008656",
        "legal_name": "METALURGICA GERDAU S.A.",
        "category": "FRE - Formulário de Referência",
        "doc_type": "",
        "species": "",
        "subject": "",
        "modality": "AP",
        "status": SourceStatus.ACTIVE,
        "delivery_date": date(2026, 8, 27),
        "reference_date": date(2026, 12, 31),
    }
    values.update(overrides)
    return SourceDocument(**values)


class FakeClient:
    """The two disciplined exchanges the chain uses, and a record of how it used them."""

    def __init__(
        self,
        *,
        viewer: str | None = None,
        menu: Any = None,
        answers: list[dict[str, Any]] | None = None,
    ) -> None:
        self.viewer = viewer_page() if viewer is None else viewer
        self.menu = section_tree("1000", "1030") if menu is None else menu
        self.answers = answers or [generator_control(padded_pdf())]
        self.pages: list[tuple[str, dict[str, str], str | None]] = []
        self.methods: list[tuple[str, dict[str, Any], str]] = []

    def open_page(
        self, path: str, params: dict[str, str], *, referer: str | None
    ) -> tuple[str, str]:
        self.pages.append((path, dict(params), referer))
        return (self.viewer if "FRE" in path else "<html></html>"), f"https://example/{path}"

    def call_page_method(self, path: str, payload: dict[str, Any], *, referer: str) -> Any:
        self.methods.append((path, payload, referer))
        if path.endswith("CarregarMenuRelatorios"):
            return self.menu
        return self.answers.pop(0)


def test_only_the_category_this_build_knows_a_page_for_has_a_reading_copy() -> None:
    assert has_reading_page(document()) is True
    assert has_reading_page(document(category="ITR - Informações Trimestrais")) is False
    assert has_reading_page(document(category="Fato Relevante")) is False


def test_the_chain_is_walked_in_order_and_every_hop_names_the_one_before_it() -> None:
    client = FakeClient()

    reading_pdf(client, document())

    assert [path for path, _, _ in client.pages] == [
        "frmConsultaExternaCVM.aspx",
        "frmGerenciaPaginaFRE.aspx",
        "frmRelatorioPDF.aspx",
    ]
    # The entry page is the front door and refers to nothing; everything after it names the
    # page it was reached from, which is the credential the generator actually checks.
    assert [referer for _, _, referer in client.pages] == [
        None,
        "https://example/frmConsultaExternaCVM.aspx",
        "https://example/frmGerenciaPaginaFRE.aspx",
    ]
    assert [path for path, _, _ in client.methods] == [
        "frmRelatorioPDF.aspx/CarregarMenuRelatorios",
        "frmRelatorioPDF.aspx/GerarRelatorio",
    ]


def test_the_generated_report_is_asked_for_by_the_sections_the_source_listed() -> None:
    client = FakeClient(menu=section_tree("1000", "1030", "1060"))

    reading_pdf(client, document())

    _, payload, _ = client.methods[-1]
    assert payload["reportIds"] == "0,1000,1030,1060"
    assert payload["nrSequencialDocumento"] == "161120"
    assert payload["codigoCVM"] == "008656"
    assert payload["tipoDocumento"] == "FREWEB"


def test_the_document_ends_at_its_last_eof_and_never_at_what_the_source_reports() -> None:
    client = FakeClient(answers=[generator_control(padded_pdf(DOCUMENT_PDF))])

    content = reading_pdf(client, document())

    assert content == DOCUMENT_PDF
    assert len(content) < READING_BUFFER_BYTES


def test_a_document_delivered_over_several_reads_is_assembled_whole() -> None:
    half = len(DOCUMENT_PDF) // 2
    client = FakeClient(
        answers=[
            generator_control(
                DOCUMENT_PDF[:half], Finalizado=False, BytesLidos=half
            ),
            generator_control(padded_pdf(DOCUMENT_PDF)[half:], BytesLidos=READING_BUFFER_BYTES),
        ]
    )

    assert reading_pdf(client, document()) == DOCUMENT_PDF
    assert [path for path, _, _ in client.methods][-1].endswith("ContinuarLeituraRelatorio")


def test_a_continuation_echoes_the_position_and_never_the_bytes_already_held() -> None:
    client = FakeClient(
        answers=[
            generator_control(DOCUMENT_PDF[:5], Finalizado=False, BytesLidos=5),
            generator_control(padded_pdf(DOCUMENT_PDF)[5:]),
        ]
    )

    reading_pdf(client, document())

    _, payload, _ = client.methods[-1]
    assert payload["controleGeracaoRelatorioPDF"]["ConteudoLido"] is None
    assert payload["controleGeracaoRelatorioPDF"]["BytesLidos"] == 5


def test_a_generator_that_stops_advancing_is_transient_and_not_a_loop() -> None:
    stalled = generator_control(b"", Finalizado=False, BytesLidos=7)
    client = FakeClient(answers=[stalled, dict(stalled), dict(stalled)])

    with pytest.raises(TransientSourceError, match="stopped advancing"):
        reading_pdf(client, document())


def test_an_interactive_captcha_demand_is_terminal_and_never_retried() -> None:
    client = FakeClient(answers=[generator_control(None, Finalizado=False, V2=True)])

    with pytest.raises(CaptchaRequiredError, match="interactive captcha"):
        reading_pdf(client, document())


def test_a_page_that_would_attach_a_token_stops_before_it_asks_for_anything() -> None:
    client = FakeClient(viewer=viewer_page(hdnHabilitaCaptcha="S"))

    with pytest.raises(CaptchaRequiredError, match="captcha token"):
        reading_pdf(client, document())
    # Stopped at the viewer: the generator was never reached, which is the point of reading
    # the switch instead of finding out by asking.
    assert client.methods == []


def test_a_third_spelling_of_the_captcha_switch_is_divergence_and_not_a_quiet_no() -> None:
    client = FakeClient(viewer=viewer_page(hdnHabilitaCaptcha="Talvez"))

    with pytest.raises(SourceContractError, match="hdnHabilitaCaptcha"):
        reading_pdf(client, document())


def test_the_generators_own_refusal_travels_verbatim() -> None:
    message = "ERRO: Por favor, acesse este conteúdo pela página principal dos documentos"
    client = FakeClient(answers=[generator_control(None, Erro=True, MensagemErro=message)])

    with pytest.raises(TransientSourceError, match="página principal"):
        reading_pdf(client, document())


def test_a_page_that_is_not_the_viewer_is_said_so_before_four_more_requests() -> None:
    client = FakeClient(viewer="<html><body>erro</body></html>")

    with pytest.raises(TransientSourceError, match="hdnHash"):
        reading_pdf(client, document())
    assert client.methods == []


def test_a_buffer_that_is_not_a_pdf_is_refused_on_its_signature() -> None:
    client = FakeClient(answers=[generator_control(padded_pdf(b"<html>erro</html>\n%%EOF"))])

    with pytest.raises(SourceContractError, match="signature is not a PDF"):
        reading_pdf(client, document())


def test_a_buffer_with_no_end_marker_is_refused_rather_than_trimmed_on_a_guess() -> None:
    client = FakeClient(answers=[generator_control(b"%PDF-1.4\nno end at all\n")])

    with pytest.raises(SourceContractError, match="cannot tell where the document ends"):
        reading_pdf(client, document())


def test_a_tail_that_is_not_padding_is_an_unmeasured_shape() -> None:
    tail = DOCUMENT_PDF + b"\n" + b"\x00" * 20 + b"trailing data"
    client = FakeClient(answers=[generator_control(tail)])

    with pytest.raises(SourceContractError, match="that are not padding"):
        reading_pdf(client, document())


def test_a_body_that_is_not_the_base64_it_is_delivered_as_is_divergence() -> None:
    client = FakeClient(answers=[generator_control(None, ConteudoLido="not base64 at all!!")])

    with pytest.raises(SourceContractError, match="not the base64"):
        reading_pdf(client, document())


def test_a_tree_with_no_section_would_generate_nothing_and_says_so() -> None:
    client = FakeClient(menu='{"id": "#", "children": []}')

    with pytest.raises(DocumentError, match="no section at all"):
        reading_pdf(client, document())


def test_a_tree_that_is_not_json_is_divergence() -> None:
    client = FakeClient(menu="<html>erro</html>")

    with pytest.raises(SourceContractError, match="not JSON"):
        reading_pdf(client, document())


def test_the_position_the_source_reports_is_read_as_a_number_or_not_at_all() -> None:
    client = FakeClient(
        answers=[generator_control(b"", Finalizado=False, BytesLidos="16777216")]
    )

    with pytest.raises(SourceContractError, match="BytesLidos"):
        reading_pdf(client, document())


def test_the_measured_answer_of_the_real_source_decodes_to_the_document_it_carried() -> None:
    # The shape measured 2026-08-28: base64 in one field, ``Finalizado`` on the first answer,
    # ``BytesLidos`` reporting the buffer and ``TamanhoTotal`` reporting nothing at all.
    measured = generator_control(padded_pdf(DOCUMENT_PDF, buffer=4096), BytesLidos=4096)
    assert measured["TamanhoTotal"] == 0
    assert len(base64.b64decode(measured["ConteudoLido"])) == 4096

    assert reading_pdf(FakeClient(answers=[measured]), document()) == DOCUMENT_PDF
