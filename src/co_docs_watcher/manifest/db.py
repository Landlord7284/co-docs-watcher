"""SQLite: connection, pragmas, and versioned migrations.

The manifest is the only durable memory this system has. The filesystem and SQLite do not form
a single transaction, so idempotency rests here plus startup reconciliation — never on whether
a file happens to exist.

Two decisions worth stating out loud:

*A newer schema refuses to open.* An older build reading a database written by a newer one
would keep working and quietly write rows the newer build cannot interpret. Degrading is worse
than stopping.

*No HTTP request inside an open transaction.* The source is slow and fragile; a transaction
held open across a request pins a WAL frame and blocks every writer for as long as the CVM
takes to answer. Collect pages in memory, then write.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from co_docs_watcher.errors import ManifestError, SchemaTooNewError

__all__ = [
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "apply_migrations",
    "connect",
    "open_manifest",
    "schema_version",
    "transaction",
]

logger = logging.getLogger(__name__)

PRAGMAS: tuple[tuple[str, str], ...] = (
    # Readers never block the writer: the inbox can be regenerated while a fetch is running.
    ("journal_mode", "WAL"),
    # Durability traded for speed at the fsync level only; a crash loses at most the last
    # transaction, and reconciliation on the next start recovers from exactly that.
    ("synchronous", "NORMAL"),
    # Off by default in SQLite, and every table here leans on it for cascade deletes.
    ("foreign_keys", "ON"),
    # Concurrent runs are refused by the lock, but the inbox writer and a reader can still meet.
    ("busy_timeout", "30000"),
)

#: Statements per schema version. Index 0 is version 1; appending here bumps SCHEMA_VERSION.
MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        """
        CREATE TABLE documents (
            document_id     INTEGER NOT NULL,
            version         INTEGER NOT NULL,
            protocol        TEXT    NOT NULL,
            cvm_code        TEXT    NOT NULL,
            legal_name      TEXT    NOT NULL,
            category        TEXT    NOT NULL,
            doc_type        TEXT    NOT NULL,
            species         TEXT    NOT NULL,
            subject         TEXT    NOT NULL,
            modality        TEXT    NOT NULL,
            status          TEXT    NOT NULL,
            delivery_date   TEXT    NOT NULL,
            reference_date  TEXT,
            local_state     TEXT    NOT NULL,
            archive_path    TEXT,
            first_seen_at   TEXT    NOT NULL,
            last_seen_at    TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL,
            PRIMARY KEY (document_id, version)
        )
        """,
        # The window sweep, the purge and the inbox all query by delivery date.
        "CREATE INDEX documents_by_delivery_date ON documents (delivery_date)",
        # The fetch queue is "everything still discovered".
        "CREATE INDEX documents_by_local_state ON documents (local_state)",
        # The inbox groups a day by company.
        "CREATE INDEX documents_by_company ON documents (cvm_code, delivery_date)",
        """
        CREATE TABLE document_files (
            document_id     INTEGER NOT NULL,
            version         INTEGER NOT NULL,
            relative_path   TEXT    NOT NULL,
            role            TEXT    NOT NULL,
            sha256          TEXT    NOT NULL,
            size_bytes      INTEGER NOT NULL,
            stable          INTEGER NOT NULL,
            recorded_at     TEXT    NOT NULL,
            PRIMARY KEY (document_id, version, relative_path),
            FOREIGN KEY (document_id, version)
                REFERENCES documents (document_id, version) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE download_attempts (
            attempt_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id     INTEGER NOT NULL,
            version         INTEGER NOT NULL,
            attempted_at    TEXT    NOT NULL,
            outcome         TEXT    NOT NULL,
            detail          TEXT,
            FOREIGN KEY (document_id, version)
                REFERENCES documents (document_id, version) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX download_attempts_by_document ON download_attempts (document_id, version)",
        """
        CREATE TABLE sync_state (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """,
    ),
)

#: The schema this build understands. Anything higher on disk is refused.
SCHEMA_VERSION = len(MIGRATIONS)


def connect(path: Path) -> sqlite3.Connection:
    """Open a connection with the pragmas applied. Does not migrate.

    ``isolation_level=None`` turns off the driver's implicit transactions: writes are explicit,
    through :func:`transaction`, so it is always visible where one begins and ends.

    Opening covers the pragmas and not just the driver call, because ``sqlite3.connect`` never
    touches the file: the first read of it is the first pragma, and that is where a manifest that
    is not a database announces itself. A failure at either end leaves no connection behind and
    reaches the operator as a :class:`ManifestError`, which is what carries a documented exit code.
    """
    path = Path(path)
    connection: sqlite3.Connection | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        journal_mode = _apply_pragmas(connection)
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise ManifestError(f"{path}: cannot open the manifest: {exc}") from exc
    if journal_mode != "wal":
        connection.close()
        raise ManifestError(
            f"{path}: the manifest opened in journal mode {journal_mode!r} instead of WAL. "
            "That is the answer of a filesystem that cannot support it, and data_root must live "
            "on a filesystem local to the process."
        )
    return connection


def _apply_pragmas(connection: sqlite3.Connection) -> str:
    """Apply :data:`PRAGMAS` and return the journal mode actually in force.

    ``journal_mode`` is the one pragma here that can fail without failing: SQLite answers with the
    mode it settled on rather than with an error, so a filesystem that cannot support WAL leaves
    the manifest in rollback journalling and says nothing. Readers never blocking the writer is
    what this database is designed around, so the answer is read back rather than assumed.
    """
    journal_mode = ""
    for pragma, value in PRAGMAS:
        row = connection.execute(f"PRAGMA {pragma} = {value}").fetchone()
        if pragma == "journal_mode":
            journal_mode = str(row[0]).lower() if row is not None else "unknown"
    return journal_mode


def open_manifest(path: Path) -> sqlite3.Connection:
    """Open the manifest and bring it to the current schema version."""
    connection = connect(path)
    try:
        apply_migrations(connection)
    except Exception:
        connection.close()
        raise
    return connection


def schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(connection: sqlite3.Connection) -> int:
    """Migrate to :data:`SCHEMA_VERSION`, one version at a time.

    Already-current databases are left untouched, so calling this on every start costs a single
    pragma read. Each step is applied inside its own transaction with the version bump, so an
    interrupted migration leaves the database at the last version that fully applied.
    """
    current = schema_version(connection)
    if current > SCHEMA_VERSION:
        raise SchemaTooNewError(
            f"manifest schema version {current} was written by a newer build; this build "
            f"understands up to {SCHEMA_VERSION}. Upgrade the watcher instead of downgrading "
            "the archive."
        )
    for target in range(current + 1, SCHEMA_VERSION + 1):
        logger.info("migrating the manifest to schema version %d", target)
        with transaction(connection):
            for statement in MIGRATIONS[target - 1]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {target}")
    return SCHEMA_VERSION


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """An explicit write transaction. ``BEGIN IMMEDIATE`` takes the write lock up front.

    Nothing that talks to the network belongs inside this block.

    The commit is guarded like the body is, because a transaction left open outlives the write that
    failed: the next ``BEGIN IMMEDIATE`` on the same connection is refused, and one document that
    could not be committed would take every write after it with it. A rollback that itself fails is
    reported and never replaces the failure that caused it.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        _rollback(connection)
        raise


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error as exc:
        logger.error("the manifest transaction could not be rolled back: %s", exc)
