"""The row parser is strict: twelve fields, sort keys, and the download call — or nothing."""

from __future__ import annotations

from datetime import date

import pytest

from co_docs_watcher.errors import SourceContractError
from co_docs_watcher.models import SourceStatus
from co_docs_watcher.rad.schema import (
    FIELD_COUNT,
    FIELD_SEPARATOR,
    ROW_SEPARATOR,
    parse_listing,
    parse_row,
)
from tests import rad

# --- Contract: recorded rows, captured 2026-08-24, parse field by field. ---


def test_a_recorded_eventual_filing_parses_field_by_field() -> None:
    document = parse_row(rad.RECORDED_ACTIVE)

    assert document.document_id == 1084789
    assert document.version == 1
    assert document.protocol == "1560083"
    assert document.identity == (1084789, 1)
    assert document.cvm_code == "002437"
    assert document.legal_name == "AXIA ENERGIA S.A."
    assert document.category == "Fato Relevante"
    assert document.doc_type == ""
    assert document.species == "Processo Judicial movido pelo Estado do Piauí"
    assert document.subject == "Processo Judicial movido pelo Estado do Piauí"
    assert document.modality == "AP"
    assert document.status is SourceStatus.ACTIVE
    assert document.delivery_date == date(2026, 8, 21)
    assert document.reference_date == date(2026, 8, 21)


def test_a_recorded_structured_document_parses_field_by_field() -> None:
    document = parse_row(rad.RECORDED_STRUCTURED)

    assert document.document_id == 161032
    assert document.protocol == "025925ITR300620260100161032-78"
    assert document.cvm_code == "025925"
    assert document.category == "ITR - Informações Trimestrais"
    assert document.species == ""
    assert document.subject == ""
    assert document.delivery_date == date(2026, 8, 21)
    assert document.reference_date == date(2026, 6, 30)


def test_recorded_inactive_and_cancelled_rows_keep_their_status_and_download_arguments() -> None:
    inactive = parse_row(rad.RECORDED_INACTIVE)
    cancelled = parse_row(rad.RECORDED_CANCELLED)

    assert inactive.status is SourceStatus.INACTIVE
    assert inactive.protocol == "1560098"
    assert cancelled.status is SourceStatus.CANCELLED
    assert cancelled.protocol == "1560076"
    assert cancelled.subject == "Processo Judicial movido pelo Estado do Piauí"


def test_a_recorded_payload_parses_every_row() -> None:
    documents = parse_listing(rad.payload(*rad.RECORDED_ROWS))

    assert [d.document_id for d in documents] == [1084789, 161032, 1084804, 1084782]


# --- The twelve-field rule. ---


def test_the_trailing_row_separator_leaves_no_phantom_row() -> None:
    assert len(parse_listing(rad.payload(rad.row()))) == 1


def test_an_empty_payload_is_an_empty_day() -> None:
    assert parse_listing("") == []


def test_a_row_with_eleven_fields_aborts_the_collection() -> None:
    truncated = FIELD_SEPARATOR.join(rad.row().split(FIELD_SEPARATOR)[:-1])

    with pytest.raises(SourceContractError, match="expected 12 fields, got 11"):
        parse_listing(rad.payload(rad.row(), truncated))


def test_a_subject_containing_the_field_separator_aborts_the_collection() -> None:
    # Nothing is escaped on this wire: the extra separator is indistinguishable from a
    # thirteenth field, and a silently shifted row would be worse than no collection.
    poisoned = rad.row(subject="lucro de R$&nbsp;1 bi")
    assert len(poisoned.split(FIELD_SEPARATOR)) == FIELD_COUNT + 1

    with pytest.raises(SourceContractError, match="expected 12 fields, got 13"):
        parse_listing(rad.payload(poisoned))


def test_a_subject_containing_the_row_separator_aborts_the_collection() -> None:
    with pytest.raises(SourceContractError, match="fields"):
        parse_listing(rad.payload(rad.row(subject="a $&&* b")))


# --- Sort keys. ---


def test_an_empty_reference_key_means_no_reference_date() -> None:
    assert parse_row(rad.row(reference="")).reference_date is None


def test_a_missing_span_order_tag_on_a_date_field_aborts() -> None:
    fields = rad.row().split(FIELD_SEPARATOR)
    fields[6] = "21/08/2026 15:37"

    with pytest.raises(SourceContractError, match="no <spanOrder> sort key"):
        parse_row(FIELD_SEPARATOR.join(fields))


def test_a_sort_key_that_is_not_yyyymmdd_aborts() -> None:
    with pytest.raises(SourceContractError, match="not yyyymmdd"):
        parse_row(rad.row(delivery="21/08/2026"))


def test_a_sort_key_that_is_not_a_real_date_aborts() -> None:
    with pytest.raises(SourceContractError, match="not a real date"):
        parse_row(rad.row(delivery="20261341"))


def test_an_empty_delivery_key_aborts() -> None:
    with pytest.raises(SourceContractError, match="no delivery date"):
        parse_row(rad.row(delivery=""))


def test_the_display_format_is_never_what_gets_parsed() -> None:
    # The sort key and the display disagree; the sort key must win.
    document = parse_row(rad.row(delivery="20260820", delivery_display="21/08/2026 15:37"))

    assert document.delivery_date == date(2026, 8, 20)


# --- Enumerated fields. ---


def test_an_unknown_status_aborts() -> None:
    with pytest.raises(SourceContractError, match="not a known status"):
        parse_row(rad.row(status="Pendente"))


def test_an_unknown_modality_aborts() -> None:
    with pytest.raises(SourceContractError, match="not a known modality"):
        parse_row(rad.row(modality="XX"))


def test_a_version_that_is_not_an_integer_aborts() -> None:
    fields = rad.row().split(FIELD_SEPARATOR)
    fields[8] = "sete"

    with pytest.raises(SourceContractError, match="not an integer"):
        parse_row(FIELD_SEPARATOR.join(fields))


def test_a_cvm_code_that_is_not_a_code_aborts() -> None:
    with pytest.raises(SourceContractError, match="not a CVM code"):
        parse_row(rad.row(cvm_code="—"))


def test_the_hyphenated_cvm_code_is_normalized_to_six_digits() -> None:
    assert parse_row(rad.row(cvm_code="00951-2")).cvm_code == "009512"


# --- The download call. ---


def test_a_row_without_the_download_call_aborts() -> None:
    with pytest.raises(SourceContractError, match="no OpenDownloadDocumentos"):
        parse_row(rad.row(actions="<i class='fi-page-search'> </i>"))


def test_the_unquoted_spelling_of_the_call_is_tolerated() -> None:
    document = parse_row(
        rad.row(actions=rad.action_icons(160125, 1, "009512FRE202620260700160125-70", quoted=False))
    )

    assert document.document_id == 160125
    assert document.protocol == "009512FRE202620260700160125-70"


def test_a_version_disagreement_between_field_8_and_the_call_aborts() -> None:
    with pytest.raises(SourceContractError, match="field 8 says version 2"):
        parse_row(rad.row(version=2, actions=rad.action_icons(version=1)))


def test_separators_are_what_the_wire_uses() -> None:
    # Pinned literally: these values are the contract, not a configuration.
    assert ROW_SEPARATOR == "$&&*"
    assert FIELD_SEPARATOR == "$&"
    assert FIELD_COUNT == 12
