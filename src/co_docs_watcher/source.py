"""The seam: how the pipeline consumes the source without knowing what the source is.

The pipeline depends on this protocol, never on ``rad/``. ``run.py`` is the composition root
and the only module in ``src/`` allowed to import the adapter that implements it — a rule an
architecture test enforces on every CI run.

The protocol is deliberately narrow. Discovery is a global sweep, one request per day, and no
per-company query path exists; filtering against the watch list happens locally, on the
caller's side. Downloading is one call for every category — only the content differs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

from co_docs_watcher.models import Delivery, SourceDocument

__all__ = ["Source"]


@runtime_checkable
class Source(Protocol):
    """The listing and download surface of a document source."""

    def list_window(self, days: Sequence[date]) -> list[SourceDocument]:
        """Return every publication delivered on the given days, whole market.

        One request per day. Every status is returned — ``ACTIVE``, ``INACTIVE`` and
        ``CANCELLED`` — because status is not a server-side filter and a cancellation
        arriving for free is news, not noise.
        """
        ...

    def download(self, document: SourceDocument, into: Path) -> Delivery:
        """Fetch one document into the staging directory ``into`` and describe what landed.

        Validates the content, extracts containers, and writes nothing outside ``into``.
        Naming and placement in the archive belong to the caller.
        """
        ...
