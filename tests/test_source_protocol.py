"""The seam is a protocol: anything shaped like a source satisfies it, without importing rad/."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from co_docs_watcher.models import Delivery, DeliveryKind, SourceDocument
from co_docs_watcher.source import Source
from tests.test_models import make_document


class FakeSource:
    """A stand-in with no relationship to RAD whatsoever — that is the point of the seam."""

    def list_window(self, days: Sequence[date]) -> list[SourceDocument]:
        return [make_document(delivery_date=day) for day in days]

    def download(self, document: SourceDocument, into: Path) -> Delivery:
        return Delivery(document=document, kind=DeliveryKind.PDF, files=())


def test_a_structurally_compatible_object_is_a_source() -> None:
    assert isinstance(FakeSource(), Source)


def test_an_incomplete_object_is_not() -> None:
    class Halfway:
        def list_window(self, days: Sequence[date]) -> list[SourceDocument]:
            return []

    assert not isinstance(Halfway(), Source)


def test_the_sweep_is_per_day_over_the_window() -> None:
    window = [date(2026, 8, 22), date(2026, 8, 23), date(2026, 8, 24)]
    source: Source = FakeSource()
    assert [document.delivery_date for document in source.list_window(window)] == window
