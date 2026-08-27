"""The discovery sweep: one global request per day of the window.

There is no per-company query path, on purpose. The CVM code arrives in field 0 of every
row, so one whole-market request per day serves a watch list of any size, and filtering
against it happens locally in the pipeline — never here and never on the server. The
category filter stays wide open for the same reason: a wrong category code returns zero
rows with no error, indistinguishable from a quiet market, and a filter this system never
sends is a trap it can never step into.

Every status comes back — ``Ativo``, ``Inativo`` and ``Cancelado`` — because status is not
a server-side filter either, and a cancellation arriving for free is news, not noise.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date

from co_docs_watcher.errors import SourceContractError
from co_docs_watcher.models import SourceDocument
from co_docs_watcher.rad.client import RadClient
from co_docs_watcher.rad.schema import parse_listing

__all__ = ["sweep"]

logger = logging.getLogger(__name__)


def sweep(client: RadClient, days: Sequence[date]) -> list[SourceDocument]:
    """Every publication delivered on the given days, whole market, every status.

    Exactly one request per day, in the order given — the window hands its dates most
    recent first, so a run that dies partway through has seen the days a reader is waiting
    on rather than the days purge is about to reach. A failure is not caught here: a missing
    day is not an isolated item, and whether the run survives it is the caller's decision.
    """
    documents: list[SourceDocument] = []
    for day in days:
        payload = client.list_documents(day)
        try:
            found = parse_listing(payload)
        except SourceContractError as error:
            # The row number is the parser's; which request produced it is only known here,
            # and a divergence reported without it sends a reader to re-fetch seven days to
            # find out which one to look at.
            raise SourceContractError(f"listing for {day.isoformat()}: {error}") from error
        logger.info("listing: %s delivered %d documents, whole market", day, len(found))
        documents.extend(found)
    return documents
