"""One guarded SQLite connection, shared by every repository.

Before this module existed, :class:`core.store.TaskStore` owned three jobs at
once: the connection, the schema DDL, and the task/history repository.  Adding a
second domain (projects) would have meant either one enormous store or two
connections to the same file — so the connection moved here, the schema moved to
:mod:`core.migrations`, and the repositories became thin.

Responsibilities
----------------
* Own **one** connection (``check_same_thread=False``) behind an ``RLock``.
* Apply the PRAGMAs (WAL, ``synchronous=NORMAL``, ``busy_timeout``, foreign keys).
* Run :mod:`core.migrations`' versioned ledger.
* Quarantine a corrupt database instead of losing the user's queue.

It deliberately knows nothing about tasks, history or projects — repositories do.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.migrations import (  # noqa: F401  (re-exported for repositories)
    DEFAULT_PROJECT_ID,
    DEFAULT_PROJECT_NAME,
    MIGRATIONS,
    SCHEMA_VERSION,
)

logger = logging.getLogger("n13")

_CORRUPTION_HINTS = (
    "not a database",
    "malformed",
    "disk image",
    "file is encrypted",
    "unsupported file format",
)


def is_corruption_error(exc: BaseException) -> bool:
    """True only when *exc* indicates an unrecoverable/corrupt database file.

    ``OperationalError`` is a subclass of ``DatabaseError``, so a class check
    alone is too broad — "database is locked" and "disk is full" are runtime
    conditions, not corruption.  Detection is therefore message-based plus a
    precise check for a bare ``sqlite3.DatabaseError`` (always structural).
    """
    msg = str(exc).lower()
    if any(hint in msg for hint in _CORRUPTION_HINTS):
        return True
    return type(exc) is sqlite3.DatabaseError


class _ClosedCursor:
    """Cursor stand-in returned after the database is closed (no-op).

    Also yielded by :meth:`Database.transaction` when the database is already
    closed, so callers that compose statements inside a transaction keep working
    instead of raising ``AttributeError`` on a half-dead handle.
    """

    rowcount = 0

    def __iter__(self):
        return iter(())

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def execute(self, *_args, **_kwargs) -> "_ClosedCursor":
        return self

    def executemany(self, *_args, **_kwargs) -> "_ClosedCursor":
        return self


class Database:
    """One guarded SQLite connection plus the migration runner."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = self._connect()
        try:
            self._prepare()
        except BaseException as exc:  # noqa: BLE001 - re-raised unless corrupt
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            if is_corruption_error(exc):
                self._conn = self._recover_from_corruption(exc)
            else:
                # Not corruption (locked / permission / disk full / filesystem
                # unavailable) — surface the real error, never hide it.
                raise

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _prepare(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self.run_migrations()

    def _recover_from_corruption(self, original: BaseException) -> sqlite3.Connection:
        """Quarantine the corrupt file (never delete it) and start fresh."""
        quarantine = self._quarantine_path()
        try:
            for side in ("", "-wal", "-shm", "-journal"):
                src = Path(str(self._path) + side)
                if src.exists():
                    src.rename(Path(str(quarantine) + side))
        except OSError as move_exc:
            # Could not quarantine — do not silently proceed with a broken db.
            raise move_exc from original

        logger.error(
            "Task database was corrupt (%s); quarantined to %s and rebuilt.",
            original,
            quarantine,
        )
        self._conn = self._connect()
        self._prepare()
        return self._conn

    def _quarantine_path(self) -> Path:
        """Deterministic, collision-safe quarantine name."""
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = Path(str(self._path) + f".corrupt-{ts}")
        candidate = base
        counter = 1
        while candidate.exists() or Path(str(candidate) + "-wal").exists():
            candidate = base.with_name(f"{base.name}-{counter}")
            counter += 1
        return candidate

    # ------------------------------------------------------------------ #
    # Migrations
    # ------------------------------------------------------------------ #

    def schema_version(self) -> int:
        with self._lock:
            if self._closed:
                return 0
            try:
                row = self._conn.execute("PRAGMA user_version").fetchone()
                return int(row[0]) if row else 0
            except sqlite3.Error:
                return 0

    def run_migrations(self) -> int:
        """Apply every migration newer than the stored ``user_version``.

        The ledger is advanced **only after** a migration's statements are
        committed, so an interrupted upgrade re-runs rather than skipping ahead.
        Each migration is individually idempotent for the same reason.
        """
        current = self.schema_version()
        applied = 0
        for version, description, migrate in MIGRATIONS:
            if version <= current:
                continue
            logger.info("Migrating database to schema v%d (%s)", version, description)
            try:
                migrate(self._conn)
                self._conn.execute("PRAGMA user_version=%d" % int(version))
                self._conn.commit()
            except BaseException:
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                raise
            applied += 1
        return applied

    # ------------------------------------------------------------------ #
    # Access
    # ------------------------------------------------------------------ #

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def closed(self) -> bool:
        return self._closed

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Run one statement and commit.  Never raises on a busy/locked db."""
        with self._lock:
            if self._closed:
                return _ClosedCursor()
            try:
                cur = self._conn.execute(sql, tuple(params))
                self._conn.commit()
                return cur
            except sqlite3.ProgrammingError:
                return _ClosedCursor()
            except sqlite3.OperationalError:
                # Locked/busy (another connection, or a broken table) — a
                # persistence failure must never crash a worker.
                return _ClosedCursor()

    def execute_raw(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Run one statement **without** swallowing errors (migrations, DDL)."""
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._conn.executemany(sql, [tuple(r) for r in rows])
                self._conn.commit()
            except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                pass

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self._lock:
            if self._closed:
                return []
            try:
                cur = self._conn.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
            except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                return []

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self):
        """Atomic multi-statement write.  Rolls back on any exception.

        Used by the multi-step project delete policies, where a partial delete
        would leave orphaned task rows behind.  Repositories accept the yielded
        connection so a service can compose several of their operations into one
        atomic unit without writing SQL itself.
        """
        with self._lock:
            if self._closed:
                yield _ClosedCursor()
                return
            try:
                self._conn.execute("BEGIN")
                yield self._conn
                self._conn.commit()
            except BaseException:
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
