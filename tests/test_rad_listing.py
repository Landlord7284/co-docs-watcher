"""The sweep is global and per-day: one request per date, no filters, every status."""

from __future__ import annotations

from datetime import date

import pytest

from co_docs_watcher.errors import SourceContractError, TransientSourceError
from co_docs_watcher.models import SourceStatus
from co_docs_watcher.rad.listing import sweep
from tests import rad


class FakeClient:
    """Records every listing call and answers from a payload per day."""

    def __init__(self, by_day: dict[date, str]) -> None:
        self.by_day = by_day
        self.calls: list[tuple[date, tuple[str, ...]]] = []

    def list_documents(self, day: date, companies: tuple[str, ...] = ()) -> str:
        self.calls.append((day, tuple(companies)))
        answer = self.by_day[day]
        if answer == "boom":
            raise TransientSourceError("the source stayed down")
        return answer


WINDOW = [date(2026, 8, 19), date(2026, 8, 20), date(2026, 8, 21)]


def test_exactly_one_request_per_day_in_window_order() -> None:
    client = FakeClient(dict.fromkeys(WINDOW, ""))

    sweep(client, WINDOW)  # type: ignore[arg-type]

    assert [day for day, _ in client.calls] == WINDOW


def test_the_sweep_never_narrows_by_company() -> None:
    # Filtering against the watch list is the pipeline's job, done locally: the whole
    # market comes back, and the CVM code in field 0 makes local routing exact.
    client = FakeClient(dict.fromkeys(WINDOW, ""))

    sweep(client, WINDOW)  # type: ignore[arg-type]

    assert all(companies == () for _, companies in client.calls)


def test_multi_day_results_aggregate_in_window_order() -> None:
    client = FakeClient(
        {
            date(2026, 8, 19): rad.payload(rad.row(document_id=1, delivery="20260819")),
            date(2026, 8, 20): "",
            date(2026, 8, 21): rad.payload(
                rad.row(document_id=2, delivery="20260821"),
                rad.row(document_id=3, delivery="20260821"),
            ),
        }
    )

    documents = sweep(client, WINDOW)  # type: ignore[arg-type]

    assert [d.document_id for d in documents] == [1, 2, 3]
    assert documents[0].delivery_date == date(2026, 8, 19)


def test_a_failed_day_propagates_instead_of_shrinking_the_window() -> None:
    client = FakeClient({date(2026, 8, 19): "", date(2026, 8, 20): "boom"})

    with pytest.raises(TransientSourceError):
        sweep(client, WINDOW)  # type: ignore[arg-type]


def test_a_divergent_row_names_the_day_it_came_from() -> None:
    # The parser counts rows inside one payload; which request produced that payload is only
    # known here, and a divergence without it sends a reader back over the whole window.
    payloads = dict.fromkeys(WINDOW, rad.payload(rad.row()))
    payloads[WINDOW[1]] = rad.payload(rad.row(modality="XX"))
    client = FakeClient(payloads)

    with pytest.raises(SourceContractError, match="listing for 2026-08-20: listing row 0"):
        sweep(client, WINDOW)  # type: ignore[arg-type]


def test_a_recorded_day_comes_back_with_every_status() -> None:
    day = date(2026, 8, 21)
    client = FakeClient({day: rad.payload(*rad.RECORDED_ROWS)})

    documents = sweep(client, [day])  # type: ignore[arg-type]

    assert {d.status for d in documents} == {
        SourceStatus.ACTIVE,
        SourceStatus.INACTIVE,
        SourceStatus.CANCELLED,
    }
    assert len(documents) == 4
