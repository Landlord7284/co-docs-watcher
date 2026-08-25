"""The FCA registry: who the companies are, independently of what they publish.

The registry answers identity questions — which CVM code belongs to a CNPJ, what a company is
called, what it is called *now* versus what it was called before, which tickers it trades
under. Nothing here knows about documents.
"""

from co_docs_watcher.cvm.registry import Registry, RegistryRecord, parse_package

__all__ = ["Registry", "RegistryRecord", "parse_package"]
