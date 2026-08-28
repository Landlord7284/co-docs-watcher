"""The download path trusts nothing: signatures decide, containers are validated whole."""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date
from pathlib import Path

import pytest

from co_docs_watcher.errors import (
    CaptchaRequiredError,
    DocumentError,
    SourceContractError,
    TransientSourceError,
)
from co_docs_watcher.models import (
    DeliveryKind,
    FileRole,
    SourceDocument,
    SourceStatus,
)
from co_docs_watcher.rad import RadSource
from co_docs_watcher.rad.download import fetch
from co_docs_watcher.source import Source
from tests.rad import generator_control, padded_pdf, section_tree, viewer_page

PDF = b"%PDF-1.6 fake body"

#: The measured member set of a structured delivery: a stable XML, the on-demand reading
#: PDF whose name carries the generation instant, and a stable spreadsheet.
STABLE_XML = "009512ITR30-06-2026v1.xml"
GENERATED_PDF = "160282_009512_24082026155035.pdf"
SPREADSHEET = "DadosDocumento.xlsx"

#: The measured shape of an eventual filing: the envelope, plus the filing itself under a
#: name that is CVM code, dates and protocol run together, with an extension the ENET
#: invented. Measured on FLEURY's 2026-08-24 Comunicado ao Mercado.
IPE_ENVELOPE = "InformacoesPeriodicasEventuais.xml"
IPE_ATTACHMENT = "021881202608242408202618072825601.ipe"


def envelope(extension: str = ".pdf") -> bytes:
    """The envelope, reduced to the one element the adapter reads out of it."""
    return (
        "<?xml version='1.0'?><Documento>"
        "<DescricaoCategoria>Comunicado ao Mercado</DescricaoCategoria>"
        f"<ExtensaoArquivo>{extension}</ExtensaoArquivo>"
        "</Documento>"
    ).encode()


class FakeClient:
    def __init__(self, content: bytes) -> None:
        self.answer = content
        self.calls: list[tuple[int, int, str]] = []

    def fetch_document(self, document_id: int, version: int, protocol: str) -> bytes:
        self.calls.append((document_id, version, protocol))
        return self.answer


def document(**overrides: object) -> SourceDocument:
    values: dict = {
        "document_id": 160282,
        "version": 1,
        "protocol": "009512ITR300620260100160282-78",
        "cvm_code": "009512",
        "legal_name": "PETROLEO BRASILEIRO S.A. PETROBRAS",
        "category": "ITR - Informações Trimestrais",
        "doc_type": "",
        "species": "",
        "subject": "",
        "modality": "AP",
        "status": SourceStatus.ACTIVE,
        "delivery_date": date(2026, 8, 21),
        "reference_date": date(2026, 6, 30),
    }
    values.update(overrides)
    return SourceDocument(**values)


def build_zip(*members: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members:
            archive.writestr(name, content)
    return buffer.getvalue()


def build_deflated_zip(*members: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members:
            archive.writestr(name, content)
    return buffer.getvalue()


def break_crc(container: bytes, original: bytes, replacement: bytes) -> bytes:
    """Rewrite a stored member's bytes in place, leaving every offset and the CRC behind.

    The container opens cleanly and the central directory is intact: the damage surfaces
    only when the member itself is read, which is the whole point of these cases.
    """
    assert len(original) == len(replacement)
    return container.replace(original, replacement)


def break_compressed_stream(container: bytes) -> bytes:
    """Damage the deflate stream of the first member, past its local header."""
    out = bytearray(container)
    with zipfile.ZipFile(io.BytesIO(container)) as archive:
        start = archive.infolist()[0].header_offset
    name_length = int.from_bytes(out[start + 26 : start + 28], "little")
    extra_length = int.from_bytes(out[start + 28 : start + 30], "little")
    at = start + 30 + name_length + extra_length
    out[at + 5] ^= 0xFF
    out[at + 9] ^= 0xFF
    return bytes(out)


def mark_encrypted(container: bytes) -> bytes:
    """Set the encryption bit of the first member, in both headers that carry it.

    ``zipfile`` cannot write an encrypted member, and ``flag_bits`` set on a ``ZipInfo`` is
    overwritten on the way out — so the bit is set on the bytes themselves.
    """
    out = bytearray(container)
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        out[out.find(signature) + offset] |= 0x01
    return bytes(out)


def staged(tmp_path: Path) -> Path:
    return tmp_path / "staging"


# --- Sniffing. ---


def test_a_pdf_is_recognized_by_its_signature_and_delivered_stable(tmp_path: Path) -> None:
    client = FakeClient(PDF)

    delivery = fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    assert delivery.kind is DeliveryKind.PDF
    (file,) = delivery.files
    assert file.role is FileRole.DOCUMENT
    assert file.stable
    assert file.path.read_bytes() == PDF
    assert file.path.parent == staged(tmp_path)


def test_the_download_uses_the_persisted_arguments(tmp_path: Path) -> None:
    client = FakeClient(PDF)

    fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    assert client.calls == [(160282, 1, "009512ITR300620260100160282-78")]


@pytest.mark.parametrize(
    "body",
    [
        b"<!DOCTYPE html><html><body>Erro</body></html>",
        b"\r\n  <html lang='pt-BR'>ASP.NET error page</html>",
        b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">ok</html>',
    ],
)
def test_an_html_body_is_rejected_even_when_well_formed(tmp_path: Path, body: bytes) -> None:
    # The source's error page arrives with HTTP 200; archiving it would archive an outage.
    client = FakeClient(body)

    with pytest.raises(TransientSourceError, match="HTML page"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    assert list(staged(tmp_path).iterdir()) == []


def test_a_binary_that_merely_contains_html_is_divergence_and_not_an_outage(
    tmp_path: Path,
) -> None:
    # A format this build cannot store needs a person to look at it. Reading it as the
    # source's error page would answer that with a backoff and three retries, and bury it.
    client = FakeClient(b"\x00\x01\x02RIFF....<html lang='x'> inside a binary header")

    with pytest.raises(SourceContractError, match="signature"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


def test_an_unknown_signature_is_contract_divergence(tmp_path: Path) -> None:
    client = FakeClient(b"\x00\x01garbage that is neither PDF nor ZIP nor HTML")

    with pytest.raises(SourceContractError, match="content signature"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


# --- Containers. ---


def test_a_structured_zip_is_extracted_with_roles_and_stability(tmp_path: Path) -> None:
    payload = build_zip(
        (STABLE_XML, b"<?xml version='1.0'?><DocumentoITR><Conta/></DocumentoITR>"),
        (GENERATED_PDF, PDF),
        (SPREADSHEET, b"not really a spreadsheet"),
    )
    client = FakeClient(payload)

    delivery = fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    assert delivery.kind is DeliveryKind.ZIP
    by_name = {file.path.name: file for file in delivery.files}
    assert set(by_name) == {STABLE_XML, GENERATED_PDF, SPREADSHEET}
    assert by_name[STABLE_XML].role is FileRole.MEMBER
    assert by_name[STABLE_XML].stable
    assert by_name[GENERATED_PDF].role is FileRole.GENERATED_PDF
    assert not by_name[GENERATED_PDF].stable
    assert by_name[SPREADSHEET].stable
    assert (staged(tmp_path) / GENERATED_PDF).read_bytes() == PDF


def test_an_ipe_package_is_unwrapped_into_the_filing_it_wraps(tmp_path: Path) -> None:
    """The ``.ipe`` extension is a wire artifact; the bytes are a PDF and the signature wins."""
    client = FakeClient(build_zip((IPE_ENVELOPE, envelope()), (IPE_ATTACHMENT, PDF)))

    delivery = fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    (file,) = delivery.files
    assert file.role is FileRole.DOCUMENT
    assert file.stable
    # The neutral staging name a bare PDF gets: the archive name is the pipeline's to impose.
    assert file.path == staged(tmp_path) / "document.pdf"
    assert file.path.read_bytes() == PDF
    # The envelope carries nothing the listing did not already give us, and is not archived.
    assert not (staged(tmp_path) / IPE_ENVELOPE).exists()
    assert not (staged(tmp_path) / IPE_ATTACHMENT).exists()


def test_an_unrecognized_attachment_falls_back_to_the_declared_extension(tmp_path: Path) -> None:
    client = FakeClient(
        build_zip((IPE_ENVELOPE, envelope(".doc")), (IPE_ATTACHMENT, b"\xd0\xcf ol"))
    )

    delivery = fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    (file,) = delivery.files
    assert file.path == staged(tmp_path) / "document.doc"
    assert file.role is FileRole.DOCUMENT


@pytest.mark.parametrize("declared", ["", "pdf", "../evil", ".p df", ".toolongextension"])
def test_an_implausible_declared_extension_leaves_the_container_whole(
    tmp_path: Path, declared: str
) -> None:
    """An envelope is data. A name it fails to justify never reaches the filesystem."""
    client = FakeClient(
        build_zip((IPE_ENVELOPE, envelope(declared)), (IPE_ATTACHMENT, b"\xd0\xcf"))
    )

    delivery = fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    assert {file.path.name for file in delivery.files} == {IPE_ENVELOPE, IPE_ATTACHMENT}
    assert all(file.role is FileRole.MEMBER for file in delivery.files)


def test_an_ipe_attachment_that_is_itself_a_container_is_archived_whole(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Reducing it would file a container as the delivery's one document, with none of the
    # checks a container gets. The nesting stays visible instead, and is said out loud.
    nested = build_zip(("dentro.pdf", PDF))
    client = FakeClient(build_zip((IPE_ENVELOPE, envelope(".zip")), (IPE_ATTACHMENT, nested)))

    with caplog.at_level(logging.WARNING):
        delivery = fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    assert sorted(file.path.name for file in delivery.files) == sorted(
        [IPE_ENVELOPE, IPE_ATTACHMENT]
    )
    assert (staged(tmp_path) / IPE_ATTACHMENT).read_bytes() == nested
    assert "is itself a container" in caplog.text


def test_an_envelope_with_several_attachments_is_archived_whole(tmp_path: Path) -> None:
    """An unmeasured shape is kept, not guessed at: discarding the envelope is irreversible."""
    client = FakeClient(
        build_zip(
            (IPE_ENVELOPE, envelope()),
            (IPE_ATTACHMENT, PDF),
            ("021881202608242408202618072825602.ipe", PDF),
        )
    )

    delivery = fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    assert len(delivery.files) == 3
    assert (staged(tmp_path) / IPE_ENVELOPE).exists()


def test_a_structured_package_is_not_mistaken_for_an_ipe_one(tmp_path: Path) -> None:
    """No envelope, no unwrapping: the ITR keeps every member it arrived with."""
    client = FakeClient(build_zip((STABLE_XML, b"<itr><conta/></itr>"), (GENERATED_PDF, PDF)))

    delivery = fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    assert {file.path.name for file in delivery.files} == {STABLE_XML, GENERATED_PDF}


def test_a_member_in_a_subdirectory_stays_inside_the_staging_directory(tmp_path: Path) -> None:
    client = FakeClient(build_zip(("anexos/carta.txt", b"ok")))

    delivery = fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    (file,) = delivery.files
    assert file.path == staged(tmp_path) / "anexos" / "carta.txt"
    assert file.path.read_bytes() == b"ok"


def test_an_empty_container_is_rejected(tmp_path: Path) -> None:
    client = FakeClient(build_zip())

    with pytest.raises(DocumentError, match="empty"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


def test_a_truncated_zip_is_transient(tmp_path: Path) -> None:
    # The signature said ZIP; the central directory never arrived. A later attempt may.
    client = FakeClient(b"PK\x03\x04 then the connection died")

    with pytest.raises(TransientSourceError, match="cannot be read"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name",
    ["../evil.pdf", "/etc/evil.pdf", "..\\evil.pdf", "a/../../evil.pdf", "C:evil.pdf"],
)
def test_zip_slip_names_are_rejected_before_anything_is_written(
    tmp_path: Path, name: str
) -> None:
    client = FakeClient(build_zip(("legit.txt", b"ok"), (name, b"evil")))

    with pytest.raises(DocumentError, match="unsafe member name"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    # Validation precedes extraction: not even the legitimate sibling was written.
    assert list(staged(tmp_path).iterdir()) == []
    assert not (tmp_path / "evil.pdf").exists()


def test_members_over_the_inflation_cap_are_rejected(tmp_path: Path) -> None:
    client = FakeClient(build_zip(("big.bin", b"x" * 4096)))

    with pytest.raises(DocumentError, match="byte cap"):
        fetch(client, document(), staged(tmp_path), max_extracted_bytes=100)  # type: ignore[arg-type]


# --- Members that cannot be read. ---
#
# The container opens and the member does not. Whatever ``zipfile`` raises at that point
# descends from nothing the pipeline catches, so an unnamed failure here does not cost one
# document — it costs the run, and the steps that had not happened yet with it.


def test_a_member_with_a_broken_crc_is_transient(tmp_path: Path) -> None:
    client = FakeClient(
        break_crc(build_zip((SPREADSHEET, b"payload")), b"payload", b"PAYLOAD")
    )

    with pytest.raises(TransientSourceError, match="could not be read"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


def test_a_member_whose_compressed_stream_is_corrupt_is_transient(tmp_path: Path) -> None:
    # A different exception entirely — ``zlib`` raises this one, not ``zipfile`` — and the
    # same news: the bytes arrived damaged and a later attempt may not.
    client = FakeClient(break_compressed_stream(build_deflated_zip((SPREADSHEET, b"x" * 4096))))

    with pytest.raises(TransientSourceError, match="could not be read"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


def test_an_encrypted_member_costs_the_document_and_not_the_run(tmp_path: Path) -> None:
    # Not transient: a member this build has no key for will not open on the next attempt
    # either. It is still one document's failure, recorded against its retry budget.
    client = FakeClient(mark_encrypted(build_zip((SPREADSHEET, b"payload"))))

    with pytest.raises(DocumentError, match="could not be extracted"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


def test_an_ipe_attachment_that_cannot_be_read_is_transient(tmp_path: Path) -> None:
    container = build_zip((IPE_ENVELOPE, envelope()), (IPE_ATTACHMENT, b"%PDF-payload"))
    client = FakeClient(break_crc(container, b"%PDF-payload", b"%PDF-PAYLOAD"))

    with pytest.raises(TransientSourceError, match="could not be read"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


# --- XML members. ---


def test_an_xml_member_that_is_html_in_disguise_is_rejected(tmp_path: Path) -> None:
    client = FakeClient(
        build_zip(("FormularioCadastral.xml", b"<html><body>error</body></html>"))
    )

    with pytest.raises(DocumentError, match="HTML page"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


def test_a_malformed_xml_member_is_rejected(tmp_path: Path) -> None:
    client = FakeClient(build_zip(("FormularioCadastral.xml", b"<<<not xml")))

    with pytest.raises(DocumentError, match="not well-formed"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


def test_an_xml_member_that_breaks_mid_read_is_transient_and_not_malformed(
    tmp_path: Path,
) -> None:
    # The bytes parse; the member's CRC is what fails, and only once the walk reaches the
    # end of it. Reported for what it is: damage in transit, not a malformed filing.
    container = build_zip((STABLE_XML, b"<Documento><Nome>ACME</Nome></Documento>"))
    client = FakeClient(break_crc(container, b"ACME", b"acme"))

    with pytest.raises(TransientSourceError, match="could not be read"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


def test_a_member_that_declares_utf8_and_delivers_latin1_is_read_anyway(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The source writes ``encoding="utf-8"`` over ISO-8859-1 bytes; the filing is whole."""
    mislabelled = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<DocumentoITR><Empresa>TRANSMISSORA ALIAN\u00c7A DE ENERGIA EL\u00c9TRICA</Empresa>"
        "</DocumentoITR>"
    ).encode("iso-8859-1")
    client = FakeClient(build_zip((STABLE_XML, mislabelled), (GENERATED_PDF, PDF)))

    with caplog.at_level(logging.WARNING):
        delivery = fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    by_name = {file.path.name: file for file in delivery.files}
    assert set(by_name) == {STABLE_XML, GENERATED_PDF}
    # Archived exactly as delivered: the wrong declaration is the publisher's, not ours.
    assert by_name[STABLE_XML].path.read_bytes() == mislabelled
    assert "declares an encoding it does not use" in caplog.text


def test_a_member_broken_under_every_encoding_still_reports_the_declared_one(
    tmp_path: Path,
) -> None:
    """The retry is for a wrong header, never a licence to accept a malformed member."""
    client = FakeClient(build_zip(("FormularioCadastral.xml", "<a>\u00e7".encode("iso-8859-1"))))

    with pytest.raises(DocumentError, match="not well-formed"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


def test_an_ipe_envelope_that_lies_about_its_encoding_still_gives_up_its_extension(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lying = (
        "<?xml version='1.0' encoding='utf-8'?><Documento>"
        "<DescricaoCategoria>Comunicado ao Mercado \u00e0s partes</DescricaoCategoria>"
        "<ExtensaoArquivo>.pdf</ExtensaoArquivo></Documento>"
    ).encode("iso-8859-1")
    # A body no signature recognizes, so the envelope's declaration is the only hint left.
    client = FakeClient(build_zip((IPE_ENVELOPE, lying), (IPE_ATTACHMENT, b"opaque bytes")))

    with caplog.at_level(logging.WARNING):
        delivery = fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]

    assert [file.path.name for file in delivery.files] == ["document.pdf"]
    # The envelope is walked twice — once to validate it, once to read the declaration —
    # and a member mis-declared once is worth saying once.
    mentions = [line for line in caplog.messages if "declares an encoding" in line]
    assert len(mentions) == 1


def test_an_xml_member_with_undefined_entities_is_rejected_not_resolved(tmp_path: Path) -> None:
    # ElementTree resolves no external entities: the reference is a parse error, never a
    # fetch of file:///etc/passwd.
    hostile = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
    client = FakeClient(build_zip(("FormularioCadastral.xml", hostile)))

    with pytest.raises(DocumentError, match="not well-formed"):
        fetch(client, document(), staged(tmp_path))  # type: ignore[arg-type]


# --- The adapter. ---


def test_the_adapter_satisfies_the_source_protocol(tmp_path: Path) -> None:
    source = RadSource(FakeClient(PDF))  # type: ignore[arg-type]

    assert isinstance(source, Source)

    delivery = source.download(document(), staged(tmp_path))
    assert delivery.kind is DeliveryKind.PDF


# --- The reading copy a container does not carry. ---

FRE_XML = "008656FRE31-12-2026v3.xml"
FRE_REGISTRATION = "FormularioCadastral.xml"
READING_PDF = b"%PDF-1.4\nreading copy\n%%EOF"


def fre_document(**overrides: object) -> SourceDocument:
    """The measured shape of an FRE delivery: two XMLs and nothing a person reads."""
    return document(
        document_id=161120,
        version=3,
        protocol="008656FRE202620260300161120-72",
        cvm_code="008656",
        legal_name="METALURGICA GERDAU S.A.",
        category="FRE - Formulário de Referência",
        **overrides,
    )


def fre_package(*extra: tuple[str, bytes]) -> bytes:
    return build_zip(
        (FRE_XML, b"<?xml version='1.0'?><DocumentoFRE><Conta/></DocumentoFRE>"),
        (FRE_REGISTRATION, b"<?xml version='1.0'?><FormularioCadastral/>"),
        *extra,
    )


class ReadingClient(FakeClient):
    """A client that also answers the chain — or refuses it in a stated way."""

    def __init__(self, content: bytes, *, refusal: Exception | None = None) -> None:
        super().__init__(content)
        self.refusal = refusal
        self.walked = 0

    def open_page(
        self, path: str, params: dict[str, str], *, referer: str | None
    ) -> tuple[str, str]:
        self.walked += 1
        if self.refusal is not None:
            raise self.refusal
        return viewer_page(), f"https://example/{path}"

    def call_page_method(self, path: str, payload: dict[str, object], *, referer: str) -> object:
        if path.endswith("CarregarMenuRelatorios"):
            return section_tree("1000")
        return generator_control(padded_pdf(READING_PDF))


def test_an_fre_delivery_gains_the_reading_copy_the_container_lacks(tmp_path: Path) -> None:
    client = ReadingClient(fre_package())

    delivery = fetch(
        client,  # type: ignore[arg-type]
        fre_document(),
        staged(tmp_path),
        want_reading_pdf=True,
    )

    assert delivery.kind is DeliveryKind.ZIP
    roles = [file.role for file in delivery.files]
    assert roles == [FileRole.MEMBER, FileRole.MEMBER, FileRole.GENERATED_PDF]
    generated = delivery.files[-1]
    # Generated on demand and named after the instant it was generated: its hash repeats for
    # nothing, which is what ``stable`` says and what keeps the manifest from claiming it.
    assert not generated.stable
    assert generated.path.read_bytes() == READING_PDF


def test_the_reading_copy_is_not_asked_for_unless_the_operator_asked(tmp_path: Path) -> None:
    client = ReadingClient(fre_package())

    delivery = fetch(client, fre_document(), staged(tmp_path))  # type: ignore[arg-type]

    assert client.walked == 0
    assert len(delivery.files) == 2


def test_a_container_that_already_carries_a_reading_copy_is_left_alone(tmp_path: Path) -> None:
    client = ReadingClient(fre_package((GENERATED_PDF, PDF)))

    delivery = fetch(
        client,  # type: ignore[arg-type]
        fre_document(),
        staged(tmp_path),
        want_reading_pdf=True,
    )

    assert client.walked == 0
    assert [file.path.name for file in delivery.files] == [
        FRE_XML,
        FRE_REGISTRATION,
        GENERATED_PDF,
    ]


def test_a_category_with_no_known_page_is_never_walked(tmp_path: Path) -> None:
    client = ReadingClient(build_zip((STABLE_XML, b"<?xml version='1.0'?><DocumentoITR/>")))

    delivery = fetch(
        client,  # type: ignore[arg-type]
        document(),
        staged(tmp_path),
        want_reading_pdf=True,
    )

    assert client.walked == 0
    assert len(delivery.files) == 1


def test_a_chain_that_fails_costs_the_reading_copy_and_never_the_filing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = ReadingClient(
        fre_package(), refusal=TransientSourceError("the generator is having a day")
    )

    with caplog.at_level(logging.WARNING):
        delivery = fetch(
            client,  # type: ignore[arg-type]
            fre_document(),
            staged(tmp_path),
            want_reading_pdf=True,
        )

    assert [file.path.name for file in delivery.files] == [FRE_XML, FRE_REGISTRATION]
    assert "having a day" in caplog.text


def test_divergence_in_the_chain_is_reported_at_its_own_severity(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Flattened to a warning, a contract that moved becomes routine and stops being read."""
    client = ReadingClient(
        fre_package(), refusal=SourceContractError("the generator answered something new")
    )

    with caplog.at_level(logging.DEBUG):
        delivery = fetch(
            client,  # type: ignore[arg-type]
            fre_document(),
            staged(tmp_path),
            want_reading_pdf=True,
        )

    assert len(delivery.files) == 2
    assert [record.levelno for record in caplog.records] == [logging.CRITICAL]


def test_a_captcha_demand_in_the_chain_is_never_swallowed(tmp_path: Path) -> None:
    client = ReadingClient(fre_package(), refusal=CaptchaRequiredError("reduce the frequency"))

    with pytest.raises(CaptchaRequiredError):
        fetch(
            client,  # type: ignore[arg-type]
            fre_document(),
            staged(tmp_path),
            want_reading_pdf=True,
        )


def test_the_adapter_carries_the_operators_answer_down_to_the_boundary(tmp_path: Path) -> None:
    client = ReadingClient(fre_package())

    source = RadSource(client, reading_pdf=True)  # type: ignore[arg-type]
    delivery = source.download(fre_document(), staged(tmp_path))

    assert delivery.files[-1].role is FileRole.GENERATED_PDF
