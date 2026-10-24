"""The SQLite database: one file per mode and account (document 03 §6).

All money and quantities are stored as decimal strings, all timestamps as UTC ISO-8601. Writes
happen in explicit ``BEGIN IMMEDIATE`` transactions, so a second writer waits up to the busy
timeout and then gets :class:`StorageBusyError` instead of a corrupt or partial write.
"""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

SCHEMA: tuple[tuple[str, ...], ...] = (
    (
        """CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            git_commit TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT
        )""",
        """CREATE TABLE orders (
            client_order_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            status TEXT NOT NULL,
            active INTEGER NOT NULL CHECK (active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            record_json TEXT NOT NULL
        )""",
        # DUP-1, enforced by the database as well: one active order per intent
        "CREATE UNIQUE INDEX one_active_order_per_intent ON orders (intent_id) WHERE active = 1",
        """CREATE TABLE order_events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            client_order_id TEXT NOT NULL REFERENCES orders (client_order_id),
            ts TEXT NOT NULL,
            event TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            detail TEXT NOT NULL
        )""",
        """CREATE TABLE journal (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL UNIQUE,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            entry_json TEXT NOT NULL
        )""",
        """CREATE TABLE halts (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            from_state TEXT NOT NULL,
            to_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            actor TEXT NOT NULL,
            transition_json TEXT NOT NULL
        )""",
        """CREATE TABLE risk_decisions (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            intent_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            approved_quantity TEXT NOT NULL,
            limits_hash TEXT NOT NULL,
            intent_json TEXT NOT NULL,
            decision_json TEXT NOT NULL
        )""",
        """CREATE TABLE leases (
            name TEXT PRIMARY KEY,
            holder TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )""",
    ),
    (
        """CREATE TABLE reconciliations (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            scope TEXT NOT NULL,
            outcome TEXT NOT NULL,
            report_json TEXT NOT NULL
        )""",
    ),
)
"""Migrations: ``SCHEMA[n]`` takes the database from version ``n`` to ``n + 1``."""


class StorageError(Exception):
    pass


class StorageBusyError(StorageError):
    """Another connection holds the write lock longer than the busy timeout."""


class StorageIntegrityError(StorageError):
    pass


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


class Database:
    def __init__(self, path: Path | str, *, busy_timeout_ms: int = 5000) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(
            self.path, isolation_level=None, timeout=busy_timeout_ms / 1000
        )
        try:
            self._conn.row_factory = sqlite3.Row
            self._conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            result = self._conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":  # pragma: no cover - needs a damaged file
                raise StorageIntegrityError(f"integrity check failed: {result}")
            self._migrate()
        except BaseException:
            self._conn.close()
            raise

    # ------------------------------------------------------------------ schema

    @property
    def schema_version(self) -> int:
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        return int(row[0])

    def _migrate(self) -> None:
        with self.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (0)")
                version = 0
            else:
                version = int(row[0])
            if version > len(SCHEMA):
                raise StorageError(
                    f"database schema {version} is newer than this program ({len(SCHEMA)})"
                )
            for statements in SCHEMA[version:]:
                for statement in statements:
                    conn.execute(statement)
            conn.execute("UPDATE schema_version SET version = ?", (len(SCHEMA),))

    # ------------------------------------------------------------------ access

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """A write transaction: committed when the block ends, rolled back on any exception."""
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if _is_busy(exc):
                raise StorageBusyError(str(exc)) from exc
            raise  # pragma: no cover - only for unexpected SQLite failures
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        try:
            self._conn.execute("COMMIT")
        except sqlite3.OperationalError as exc:  # pragma: no cover - needs a failing disk
            self._conn.execute("ROLLBACK")
            raise StorageError(f"commit failed: {exc}") from exc

    def query(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, tuple(params)).fetchall()

    def backup(self, target: Path | str) -> None:
        """Online backup to another file (consistent even while the engine writes)."""
        destination = sqlite3.connect(str(target))
        try:
            self._conn.backup(destination)
        finally:
            destination.close()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
