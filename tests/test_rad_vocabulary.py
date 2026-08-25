"""The vocabulary is copied reference data: shape-checked, never re-deduced."""

from __future__ import annotations

import re

from co_docs_watcher.rad.vocabulary import (
    ALL_DOCUMENTS,
    EVENTUAL_DOCUMENTS,
    STRUCTURED_DOCUMENTS,
)

EVENTUAL_CODE = re.compile(r"^IPE_(-1|\d+)_(-1|\d+)_(-1|\d+)$")


def test_discovery_always_requests_everything() -> None:
    assert ALL_DOCUMENTS == "EST_-1,IPE_-1_-1_-1"
    assert ALL_DOCUMENTS.split(",")[0] in STRUCTURED_DOCUMENTS
    assert ALL_DOCUMENTS.split(",")[1] in EVENTUAL_DOCUMENTS


def test_the_structured_codes_are_the_eight_measured_on_2026_08_24() -> None:
    assert set(STRUCTURED_DOCUMENTS) == {
        "EST_-1",
        "EST_1",
        "EST_2",
        "EST_3",
        "EST_4",
        "EST_6",
        "EST_11",
        "EST_13",
    }
    assert STRUCTURED_DOCUMENTS["EST_3"] == "ITR - Informações Trimestrais"
    assert STRUCTURED_DOCUMENTS["EST_4"] == "DFP - Demonstrações Financeiras Padronizadas"


def test_the_eventual_codes_are_the_524_measured_on_2026_08_24() -> None:
    assert len(EVENTUAL_DOCUMENTS) == 524
    malformed = [code for code in EVENTUAL_DOCUMENTS if not EVENTUAL_CODE.match(code)]
    assert malformed == []
    assert EVENTUAL_DOCUMENTS["IPE_44_-1_-1"].startswith("Acordo de Acionistas")


def test_the_tables_are_read_only() -> None:
    # Reference data: a mutation anywhere would be a deduction pretending to be a copy.
    for table in (STRUCTURED_DOCUMENTS, EVENTUAL_DOCUMENTS):
        try:
            table["EST_99"] = "made up"  # type: ignore[index]
        except TypeError:
            continue
        raise AssertionError("the vocabulary accepted a mutation")
