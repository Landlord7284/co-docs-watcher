"""The download path trusts nothing: signatures decide, containers are validated whole."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from co_docs_watcher.errors import DocumentError, SourceContractError, TransientSourceError
from co_docs_watcher.models import (
    DeliveryKind,
    FileRole,
    SourceDocument,
    SourceStatus,
)
from co_docs_watcher.rad import RadSource
from co_docs_watcher.rad.client import RawDownload
from co_docs_watcher.rad.download import fetch
from co_docs_watcher.source import Source

PDF = b"%PDF-1.6 fake body"

#: The measured member set of a structured delivery: a stable XML, the on-demand reading
#: PDF whose name carries the generation instant, and a stable spreadsheet.
STABLE_XML = "009512ITR30-06-2026v1.xml"
GENERATED_PDF = "160282_009512_24082026155035.pdf"
SPREADSHEET = "DadosDocumento.xlsx"


class FakeClient:
    def __init__(self, content: bytes, content_disposition: str = "") -> None:
        self.answer = RawDownload(content=content, content_disposition=content_disposition)
        self.calls: list[tuple[int, int, str]] = []

    def fetch_document(self, document_id: int, version: int, protocol: str) -> RawDownload:
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


def staged(tmp_path: Path) -> Path:
    return tmp_path / "staging"


# --- Sniffing. ---


def test_a_pdf_is_recognized_by_its_signature_and_delivered_stable(tmp_path: Path) -> None:
    client = FakeClient(PDF, content_disposition="attachment; filename=009512000101011.pdf")

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
