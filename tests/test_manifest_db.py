"""Pragmas, migrations, and the refusal to open a schema this build does not understand."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from co_docs_watcher.errors import ExitCode, ManifestError, SchemaTooNewError
from co_docs_watcher.manifest.db import (
    MIGRATIONS,
    SCHEMA_VERSION,
    apply_migrations,
    connect,
    open_manifest,
    schema_version,
    transaction,
)


def test_every_connection_applies_the_pragmas(tmp_path: Path) -> None:
    connection = connect(tmp_path / "data" / "manifest.sqlite")
    read = lambda pragma: connection.execute(f"PRAGMA {pragma}").fetchone()[0]  # noqa: E731
    assert str(read("journal_mode")).lower() == "wal"
    assert int(read("synchronous")) == 1  # NORMAL
    assert int(read("foreign_keys")) == 1
    assert int(read("busy_timeout")) == 30000
    connection.close()


def test_data_root_is_created_on_demand(tmp_path: Path) -> None:
    path = tmp_path / "data" / "manifest.sqlite"
    assert not path.parent.exists()
    connect(path).close()
    assert path.is_file()


def test_a_fresh_database_reaches_the_latest_version(tmp_path: Path) -> None:
    connection = open_manifest(tmp_path / "manifest.sqlite")
    assert schema_version(connection) == SCHEMA_VERSION
    tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"documents", "document_files", "download_attempts", "sync_state"} <= tables
    connection.close()


def test_migrating_an_up_to_date_database_is_a_no_op(tmp_path: Path) -> None:
    path = tmp_path / "manifest.sqlite"
    connection = open_manifest(path)
    connection.execute(
        "INSERT INTO sync_state (key, value, updated_at) VALUES ('watermark', '2026-08-24', 'now')"
    )
    connection.close()

    connection = open_manifest(path)
    assert schema_version(connection) == SCHEMA_VERSION
    assert connection.execute("SELECT value FROM sync_state").fetchone()["value"] == "2026-08-24"
    connection.close()


def test_stepwise_migration_equals_migrating_at_once(tmp_path: Path) -> None:
    at_once = connect(tmp_path / "at_once.sqlite")
    apply_migrations(at_once)

    stepwise = connect(tmp_path / "stepwise.sqlite")
    for target in range(1, SCHEMA_VERSION + 1):
        with transaction(stepwise):
            for statement in MIGRATIONS[target - 1]:
                stepwise.execute(statement)
            stepwise.execute(f"PRAGMA user_version = {target}")

    def schema_of(connection: sqlite3.Connection) -> list[tuple[str, str]]:
        rows = connection.execute(
            "SELECT type, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
        )
        return [(row["type"], row["sql"]) for row in rows]

    assert schema_of(stepwise) == schema_of(at_once)
    assert schema_version(stepwise) == schema_version(at_once) == SCHEMA_VERSION
    at_once.close()
    stepwise.close()


def test_a_newer_schema_refuses_to_open(tmp_path: Path) -> None:
    path = tmp_path / "manifest.sqlite"
    connection = open_manifest(path)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.close()

    with pytest.raises(SchemaTooNewError) as raised:
        open_manifest(path)
    assert raised.value.exit_code is ExitCode.INVALID_CONFIG
    assert "newer build" in str(raised.value)


def test_an_unopenable_manifest_is_a_manifest_error(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    with pytest.raises(ManifestError):
        connect(directory)


def test_foreign_keys_cascade(tmp_path: Path) -> None:
    connection = open_manifest(tmp_path / "manifest.sqlite")
    with transaction(connection):
        connection.execute(
            "INSERT INTO documents (document_id, version, protocol, cvm_code, legal_name, "
            "category, doc_type, species, subject, modality, status, delivery_date, "
            "local_state, first_seen_at, last_seen_at, updated_at) "
            "VALUES (1, 1, 'p', '009512', 'n', 'c', 't', 's', 'a', 'AP', 'active', "
            "'2026-08-24', 'available', 'now', 'now', 'now')"
        )
        connection.execute(
            "INSERT INTO document_files (document_id, version, relative_path, role, sha256, "
            "size_bytes, stable, recorded_at) VALUES (1, 1, 'x.pdf', 'document', 'h', 1, 1, 'now')"
        )
    with transaction(connection):
        connection.execute("DELETE FROM documents WHERE document_id = 1")
    assert connection.execute("SELECT count(*) AS n FROM document_files").fetchone()["n"] == 0
    connection.close()


def test_an_orphan_file_row_is_rejected(tmp_path: Path) -> None:
    connection = open_manifest(tmp_path / "manifest.sqlite")
    with pytest.raises(sqlite3.IntegrityError), transaction(connection):
        connection.execute(
            "INSERT INTO document_files (document_id, version, relative_path, role, sha256, "
            "size_bytes, stable, recorded_at) VALUES (9, 1, 'x.pdf', 'document', 'h', 1, 1, 'now')"
        )
    connection.close()


def test_a_failing_transaction_rolls_back(tmp_path: Path) -> None:
    connection = open_manifest(tmp_path / "manifest.sqlite")
    with pytest.raises(RuntimeError, match="interrupted"), transaction(connection):
        connection.execute(
            "INSERT INTO sync_state (key, value, updated_at) VALUES ('watermark', 'x', 'now')"
        )
        raise RuntimeError("interrupted")
    assert connection.execute("SELECT count(*) AS n FROM sync_state").fetchone()["n"] == 0
    connection.close()
