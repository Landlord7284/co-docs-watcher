"""The source adapter: everything that knows what RAD/ENETWeb looks like on the wire.

Nothing outside this package imports from here — the pipeline depends on the ``Source``
protocol and on the neutral models, and ``run.py`` is the single, allowlisted exception
that builds the adapter. An architecture test enforces the seam on every CI run.

Wire-format names (``temErro``, ``SolicitarCaptcha``, ``numSequencia``, ``numVersao``,
``numProtocolo``) are quoted literally inside this package and nowhere else.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from co_docs_watcher.models import Delivery, SourceDocument
from co_docs_watcher.rad.client import RadClient
from co_docs_watcher.rad.download import MAX_EXTRACTED_BYTES, fetch
from co_docs_watcher.rad.listing import sweep

__all__ = ["RadClient", "RadSource"]


class RadSource:
    """What the composition root hands to the pipeline as a ``Source``.

    Listing and download over one client, which is the point of the pairing: the two
    halves share one minimum interval and one request budget.

    ``max_extracted_bytes`` and ``reading_pdf`` are carried here because this is the last
    place that still knows them: ``Source`` is a neutral protocol and the pipeline calls
    ``download`` with a document and a directory, so a setting the composition root does not
    hand over now is a setting nothing can ever apply.
    """

    __slots__ = ("_client", "_max_extracted_bytes", "_reading_pdf")

    def __init__(
        self,
        client: RadClient,
        *,
        max_extracted_bytes: int = MAX_EXTRACTED_BYTES,
        reading_pdf: bool = False,
    ) -> None:
        self._client = client
        self._max_extracted_bytes = max_extracted_bytes
        self._reading_pdf = reading_pdf

    def list_window(self, days: Sequence[date]) -> list[SourceDocument]:
        return sweep(self._client, days)

    def download(self, document: SourceDocument, into: Path) -> Delivery:
        return fetch(
            self._client,
            document,
            into,
            max_extracted_bytes=self._max_extracted_bytes,
            want_reading_pdf=self._reading_pdf,
        )
