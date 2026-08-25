"""The steps of one run, in the order they happen.

Every step here depends on the ``Source`` protocol and on the neutral models, never on the
adapter that implements them: the pipeline does not know that the source is RAD, and an
architecture test keeps it that way.

The steps share one frontier — the ``RetentionWindow`` computed once from the clock — because
discovery, purge and inbox disagreeing about where the window starts is how a purge deletes
what the next discovery downloads again.
"""

from co_docs_watcher.pipeline.discover import DiscoveryOutcome, archive_everything, discover
from co_docs_watcher.pipeline.fetch import FetchOutcome, fetch_pending

__all__ = [
    "DiscoveryOutcome",
    "FetchOutcome",
    "archive_everything",
    "discover",
    "fetch_pending",
]
