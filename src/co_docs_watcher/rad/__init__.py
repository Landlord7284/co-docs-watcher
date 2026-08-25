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
from co_docs_watcher.rad.download import fetch
from co_docs_watcher.rad.listing import sweep

__all__ = ["RadClient", "RadSource"]


class RadSource:
    """What the composition root hands to the pipeline as a ``Source``.

    Listing and download over one client, which is the point of the pairing: the two
    halves share one minimum interval and one request budget.
    """

    __slots__ = ("_client",)

    def __init__(self, client: RadClient) -> None:
        self._client = client

    def list_window(self, days: Sequence[date]) -> list[SourceDocument]:
        return sweep(self._client, days)

    def download(self, document: SourceDocument, into: Path) -> Delivery:
        return fetch(self._client, document, into)
