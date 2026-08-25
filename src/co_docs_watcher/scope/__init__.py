"""The watch list: which companies this archive is about.

The list is a YAML file a human owns. The watcher reads it, appends to it, and removes from
it, and every rewrite must leave the human's comments, ordering and edits exactly where they
were — which is why persistence here is more careful than the size of the file suggests.
"""

from co_docs_watcher.scope.models import WatchedCompany
from co_docs_watcher.scope.resolver import resolve
from co_docs_watcher.scope.store import WatchList

__all__ = ["WatchList", "WatchedCompany", "resolve"]
