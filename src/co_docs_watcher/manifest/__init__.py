"""The manifest: what this archive knows about the documents it has seen."""

from co_docs_watcher.manifest.db import SCHEMA_VERSION, connect, open_manifest, transaction

__all__ = ["SCHEMA_VERSION", "connect", "open_manifest", "transaction"]
