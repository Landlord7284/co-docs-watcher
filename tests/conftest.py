"""Fixtures shared by the pipeline tests: a manifest, the two roots, and a fixed window."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from co_docs_watcher.clock import Clock, RetentionWindow, window_ending
from co_docs_watcher.manifest.db import open_manifest
from co_docs_watcher.manifest.repo import Manifest

#: The source's timezone, passed explicitly so no test depends on a process-wide install.
CLOCK = Clock(ZoneInfo("America/Sao_Paulo"))

#: The last day of the fixed window used across the pipeline tests.
TODAY = date(2026, 8, 24)


@dataclass(frozen=True, slots=True)
class Roots:
    """The two roots and the paths under them the pipeline writes to."""

    data_root: Path
    documents_root: Path

    @property
    def staging_root(self) -> Path:
        return self.documents_root / ".tmp"

    @property
    def inbox_root(self) -> Path:
        return self.documents_root / "_inbox"

    def day(self, day: date) -> Path:
        return self.documents_root / day.isoformat()


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    documents_root = tmp_path / "documents"
    documents_root.mkdir()
    return Roots(data_root=tmp_path / "data", documents_root=documents_root)


@pytest.fixture
def connection(roots: Roots) -> Iterator[sqlite3.Connection]:
    connection = open_manifest(roots.data_root / "manifest.sqlite")
    yield connection
    connection.close()


@pytest.fixture
def manifest(connection: sqlite3.Connection) -> Manifest:
    return Manifest.over(connection, CLOCK)


@pytest.fixture
def window() -> RetentionWindow:
    """Seven retained dates ending on :data:`TODAY` — the documented default."""
    return window_ending(TODAY, 7)
