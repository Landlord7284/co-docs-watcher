"""The neutral core: identity, completeness of the state enum, and English attribute names."""

from __future__ import annotations

from dataclasses import fields
from datetime import date
from pathlib import Path

import pytest

from co_docs_watcher.models import (
    DeliveredFile,
    Delivery,
    DeliveryKind,
    FileRole,
    LocalState,
    SourceDocument,
    SourceStatus,
)


def make_document(**overrides: object) -> SourceDocument:
    base = {
        "document_id": 160310,
        "version": 1,
        "protocol": "009512FRE202620260700160125-70",
        "cvm_code": "009512",
        "legal_name": "PETROLEO BRASILEIRO S.A. PETROBRAS",
        "category": "Fato Relevante",
        "doc_type": "Outros Comunicados",
        "species": "",
        "subject": "Petrobras informa sobre remuneracao aos acionistas",
        "modality": "AP",
        "status": SourceStatus.ACTIVE,
        "delivery_date": date(2026, 8, 24),
        "reference_date": date(2026, 12, 31),
    }
    return SourceDocument(**(base | overrides))  # type: ignore[arg-type]


def test_identity_is_document_id_and_version() -> None:
    assert make_document().identity == (160310, 1)
    assert make_document(version=2).identity == (160310, 2)


def test_a_resubmission_is_a_different_document_not_a_newer_version() -> None:
    # The source issues a new document_id for every resubmission; the pair never collides.
    original = make_document(document_id=160310, version=1)
    resubmission = make_document(document_id=160477, version=1)
    assert original.identity != resubmission.identity


def test_documents_are_frozen_and_hashable() -> None:
    document = make_document()
    assert {document, make_document()} == {document}
    with pytest.raises(AttributeError):
        document.version = 2  # type: ignore[misc]


def test_reference_date_is_optional() -> None:
    assert make_document(reference_date=None).reference_date is None


def test_the_model_carries_the_thirteen_fields_the_manifest_stores() -> None:
    assert [field.name for field in fields(SourceDocument)] == [
        "document_id",
        "version",
        "protocol",
        "cvm_code",
        "legal_name",
        "category",
        "doc_type",
        "species",
        "subject",
        "modality",
        "status",
        "delivery_date",
        "reference_date",
    ]


def test_no_wire_name_leaks_into_the_model() -> None:
    names = {field.name for field in fields(SourceDocument)}
    assert names.isdisjoint({"numSequencia", "numVersao", "numProtocolo", "temErro"})


def test_local_state_has_the_eight_states() -> None:
    assert {state.value for state in LocalState} == {
        "discovered",
        "downloading",
        "available",
        "skipped",
        "failed",
        "deactivated",
        "cancelled",
        "purged",
    }


def test_source_status_has_the_three_the_listing_always_returns() -> None:
    assert {status.value for status in SourceStatus} == {"active", "inactive", "cancelled"}


def test_a_delivery_describes_files_with_their_stability_marker() -> None:
    delivery = Delivery(
        document=make_document(),
        kind=DeliveryKind.ZIP,
        files=(
            DeliveredFile(Path("009512ITR30-06-2026v1.xml"), FileRole.MEMBER, stable=True),
            DeliveredFile(Path("160282_009512_240820261550.pdf"), FileRole.GENERATED_PDF, False),
        ),
    )
    assert [file.stable for file in delivery.files] == [True, False]
