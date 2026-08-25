"""The FCA registry: who the companies are, independently of what they publish.

The registry answers identity questions — which CVM code belongs to a CNPJ, what a company is
called, what it is called *now* versus what it was called before, which tickers it trades
under. Nothing here knows about documents.
"""

from co_docs_watcher.cvm.cache import RegistryCache
from co_docs_watcher.cvm.registry import Registry, RegistryRecord, parse_package
from co_docs_watcher.cvm.search import MatchKind, SearchResult, search
from co_docs_watcher.cvm.ticker import CompanyPrefix, PrefixSource, company_prefix, ticker_root

__all__ = [
    "CompanyPrefix",
    "MatchKind",
    "PrefixSource",
    "Registry",
    "RegistryCache",
    "RegistryRecord",
    "SearchResult",
    "company_prefix",
    "parse_package",
    "search",
    "ticker_root",
]
