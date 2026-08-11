"""Persistent task database (SQLite).

Replaces the ad-hoc ``gui_queue.json`` / ``gui_history.json`` pair as the
authoritative store for download tasks and history, while keeping the same
JSON files as a one-time migration source for existing installs.

Design goals
------------
* **Crash-safe** — WAL journaling, transactions per write, and a schema
  version pragma so the store can be migrated forward without data loss.
* **Thread-safe** — a single guarded connection (``check_same_thread=False``)
  serialises access from the queue worker, the monitor threads and the API.
* **Cheap** — only state *transitions* are written; progress ticks are
  coalesced by the caller and never touch the database.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    filename        TEXT NOT NULL DEFAULT '',
    directory       TEXT NOT NULL DEFAULT '',
    label           TEXT NOT NULL DEFAULT '',
    total_size      INTEGER NOT NULL DEFAULT 0,
    downloaded_size INTEGER NOT NULL DEFAULT 0,
    current_speed   REAL    NOT NULL DEFAULT 0,
    average_speed   REAL    NOT NULL DEFAULT 0,
    eta_seconds     REAL,
    status          TEXT NOT NULL DEFAULT 'Queued',
    priority        INTEGER NOT NULL DEFAULT 5,
    created_at      REAL NOT NULL,
    started_at      REAL,
    completed_at    REAL,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    error           TEXT NOT NULL DEFAULT '',
    connections     INTEGER NOT NULL DEFAULT 1,
    checksum        TEXT NOT NULL DEFAULT '',
    content_type    TEXT NOT NULL DEFAULT '',
    server          TEXT NOT NULL DEFAULT '',
    supports_range  INTEGER NOT NULL DEFAULT 0,
    etag            TEXT NOT NULL DEFAULT '',
    last_modified   TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL DEFAULT 'General',
    autostart       INTEGER NOT NULL DEFAULT 1,
    speed_limit_bps INTEGER NOT NULL DEFAULT 0,
    segments_json   TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    directory   TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'General',
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT '',
    duration    REAL NOT NULL DEFAULT 0,
    avg_speed   REAL NOT NULL DEFAULT 0,
    connection_mode TEXT NOT NULL DEFAULT '',
    finished    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS queue_order (
    task_id TEXT PRIMARY KEY,
    pos     INTEGER NOT NULL DEFAULT 0
);
"""


class _ClosedCursor:
    """Cursor stand-in returned after the store is closed (no-op)."""
    rowcount = 0

    def __iter__(self):
        return iter(())


_CORRUPTION_HINTS = (
    "not a database",
    "malformed",
    "disk image",
    "file is encrypted",
    "unsupported file format",
)


def _is_corruption_error(exc: BaseException) -> bool:
    """True only when *exc* indicates an unrecoverable/corrupt database file.

    ``OperationalError`` is a subclass of ``DatabaseError``, so class checks
    alone are too broad (e.g. "database is locked" or "database or disk is
    full" are *not* corruption).  Detection is therefore message-based plus a
    precise check for a bare ``sqlite3.DatabaseError`` (which is always a
    structural problem, not a runtime condition).
    """
    msg = str(exc).lower()
    if any(hint in msg for hint in _CORRUPTION_HINTS):
        return True
    return type(exc) is sqlite3.DatabaseError


class TaskStore:
    """Thread-safe SQLite store for download tasks and history."""

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._init_schema()
        except BaseException as exc:  # noqa: BLE001 - we re-raise non-corruption
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            if _is_corruption_error(exc):
                self._conn = self._recover_from_corruption(exc)
            else:
                # Not corruption (locked / permission / disk full / filesystem
                # unavailable) — surface the real error, never hide it.
                raise

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA user_version=%d" % _SCHEMA_VERSION)
            self._conn.commit()
            # Lightweight migration for existing databases created before the
            # connection_mode column existed.
            try:
                cols = [r[1] for r in self._conn.execute("PRAGMA table_info(history)")]
                if "connection_mode" not in cols:
                    self._conn.execute("ALTER TABLE history ADD COLUMN connection_mode TEXT NOT NULL DEFAULT ''")
                    self._conn.commit()
            except sqlite3.Error:
                pass

    def _recover_from_corruption(self, original: BaseException) -> "sqlite3.Connection":
        """Quarantine the corrupt database and start a fresh one.

        The corrupt file (and its WAL/SHM/journal siblings) is renamed to
        ``<name>.corrupt-<timestamp>`` — never overwritten or deleted — so it
        stays available for diagnostics.  A brand-new database with the full
        schema is created in its place and the application continues normally.
        """
        import logging

        logger = logging.getLogger("n13")
        quarantine = self._quarantine_path()
        try:
            for side in ("", "-wal", "-shm", "-journal"):
                src = Path(str(self._db_path) + side)
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
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._init_schema()
        return conn

    def _quarantine_path(self) -> Path:
        """Deterministic, collision-safe quarantine name."""
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = Path(str(self._db_path) + f".corrupt-{ts}")
        candidate = base
        counter = 1
        while candidate.exists() or Path(str(candidate) + "-wal").exists():
            candidate = base.with_name(f"{base.name}-{counter}")
            counter += 1
        return candidate

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            if self._closed:
                return _ClosedCursor()
            try:
                cur = self._conn.execute(sql, params)
                self._conn.commit()
                return cur
            except sqlite3.ProgrammingError:
                return _ClosedCursor()
            except sqlite3.OperationalError:
                # Locked/busy (another connection, or a broken table) — a
                # persistence failure must never crash a worker.
                return _ClosedCursor()

    def _query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._lock:
            if self._closed:
                return []
            try:
                cur = self._conn.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
            except sqlite3.ProgrammingError:
                return []
            except sqlite3.OperationalError:
                return []

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def _segments_to_json(segments: List[Dict[str, Any]]) -> str:
        return json.dumps(segments or [], ensure_ascii=False)

    @staticmethod
    def _json_to_segments(raw: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(raw or "[]")
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    # ------------------------------------------------------------------ #
    # Tasks
    # ------------------------------------------------------------------ #

    def save_task(
        self,
        task: Dict[str, Any],
        segments: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Upsert a task row from its dict representation."""
        segments = segments if segments is not None else task.pop("segments", None)
        if isinstance(segments, list):
            seg_json = self._segments_to_json(segments)
        else:
            seg_json = str(task.get("segments_json") or "[]")

        with self._lock:
            if self._closed:
                return
            self._conn.execute(
                """
                INSERT INTO tasks (
                    id, url, filename, directory, label, total_size,
                    downloaded_size, current_speed, average_speed, eta_seconds,
                    status, priority, created_at, started_at, completed_at,
                    retry_count, error, connections, checksum, content_type,
                    server, supports_range, etag, last_modified, category,
                    autostart, speed_limit_bps, segments_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    url=excluded.url, filename=excluded.filename,
                    directory=excluded.directory, label=excluded.label,
                    total_size=excluded.total_size,
                    downloaded_size=excluded.downloaded_size,
                    current_speed=excluded.current_speed,
                    average_speed=excluded.average_speed,
                    eta_seconds=excluded.eta_seconds,
                    status=excluded.status, priority=excluded.priority,
                    created_at=excluded.created_at, started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    retry_count=excluded.retry_count, error=excluded.error,
                    connections=excluded.connections, checksum=excluded.checksum,
                    content_type=excluded.content_type, server=excluded.server,
                    supports_range=excluded.supports_range, etag=excluded.etag,
                    last_modified=excluded.last_modified,
                    category=excluded.category, autostart=excluded.autostart,
                    speed_limit_bps=excluded.speed_limit_bps,
                    segments_json=excluded.segments_json
                """,
                (
                    task.get("id", ""),
                    task.get("url", ""),
                    task.get("filename", ""),
                    task.get("directory", ""),
                    task.get("label", ""),
                    int(task.get("total_size", 0) or 0),
                    int(task.get("downloaded_size", 0) or 0),
                    float(task.get("current_speed", 0) or 0),
                    float(task.get("average_speed", 0) or 0),
                    task.get("eta_seconds"),
                    task.get("status", "Queued"),
                    int(task.get("priority", 5) or 5),
                    float(task.get("created_at", 0) or 0),
                    task.get("started_at"),
                    task.get("completed_at"),
                    int(task.get("retry_count", 0) or 0),
                    task.get("error", ""),
                    int(task.get("connections", 1) or 1),
                    task.get("checksum", ""),
                    task.get("content_type", ""),
                    task.get("server", ""),
                    1 if task.get("supports_range") else 0,
                    task.get("etag", ""),
                    task.get("last_modified", ""),
                    task.get("category", "General"),
                    1 if task.get("autostart", True) else 0,
                    int(task.get("speed_limit_bps", 0) or 0),
                    seg_json,
                ),
            )
            self._conn.commit()

    def load_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._query_one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def list_tasks(self) -> List[Dict[str, Any]]:
        return self._query("SELECT * FROM tasks ORDER BY created_at")

    def save_order(self, task_ids: List[str]) -> None:
        """Persist the explicit queue ordering (move up/down)."""
        with self._lock:
            self._conn.execute("DELETE FROM queue_order")
            self._conn.executemany(
                "INSERT INTO queue_order (task_id, pos) VALUES (?, ?)",
                [(tid, i) for i, tid in enumerate(task_ids)],
            )
            self._conn.commit()

    def load_order(self) -> List[str]:
        rows = self._query("SELECT task_id, pos FROM queue_order ORDER BY pos")
        return [r["task_id"] for r in rows]

    def delete_task(self, task_id: str) -> None:
        self._execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def update_progress(self, task_id: str, downloaded: int, total: int) -> None:
        """Low-cost progress write used between transitions (coalesced by caller)."""
        self._execute(
            "UPDATE tasks SET downloaded_size=?, total_size=? WHERE id=?",
            (max(0, int(downloaded)), max(0, int(total)), task_id),
        )

    def clear_finished_tasks(self) -> int:
        """Delete terminal tasks (Completed/Failed/Cancelled); return count."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM tasks WHERE status IN ('Complete','Failed','Cancelled')"
            )
            self._conn.commit()
            return cur.rowcount

    def _segments_for(self, task_id: str) -> List[Dict[str, Any]]:
        row = self.load_task(task_id)
        if not row:
            return []
        return self._json_to_segments(row.get("segments_json", "[]"))

    # ------------------------------------------------------------------ #
    # History
    # ------------------------------------------------------------------ #

    def add_history(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.execute(
                """
                INSERT INTO history (
                    task_id, name, url, directory, category, size_bytes,
                    status, duration, avg_speed, connection_mode, finished
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    entry.get("task_id", ""),
                    entry.get("name", ""),
                    entry.get("url", ""),
                    entry.get("directory", ""),
                    entry.get("category", "General"),
                    int(entry.get("size_bytes", 0) or 0),
                    entry.get("status", ""),
                    float(entry.get("duration", 0) or 0),
                    float(entry.get("avg_speed", 0) or 0),
                    entry.get("connection_mode", "") or "",
                    entry.get("finished", ""),
                ),
            )
            # Keep the table bounded (matches the old 500-entry cap).
            self._conn.execute(
                "DELETE FROM history WHERE id NOT IN "
                "(SELECT id FROM history ORDER BY id DESC LIMIT 1000)"
            )
            self._conn.commit()

    def list_history(self, limit: int = 500) -> List[Dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
        )
        result = []
        for r in rows:
            result.append(
                {
                    "task_id": r.get("task_id", ""),
                    "url": r.get("url", ""),
                    "directory": r.get("directory", ""),
                    "name": r.get("name", ""),
                    "category": r.get("category", "General"),
                    "size": _human_size(int(r.get("size_bytes", 0) or 0)),
                    "size_bytes": int(r.get("size_bytes", 0) or 0),
                    "status": r.get("status", ""),
                    "duration": float(r.get("duration", 0) or 0),
                    "avg_speed": float(r.get("avg_speed", 0) or 0),
                    "connection_mode": r.get("connection_mode", "") or "",
                    "finished": r.get("finished", ""),
                }
            )
        return result

    def clear_history(self) -> None:
        self._execute("DELETE FROM history")

    def delete_history(self, task_id: str) -> None:
        self._execute("DELETE FROM history WHERE task_id=?", (task_id,))

    def clear_finished_history(self) -> None:
        self._execute(
            "DELETE FROM history WHERE status IN ('Complete','Failed','Cancelled')"
        )

    # ------------------------------------------------------------------ #
    # Migration helpers
    # ------------------------------------------------------------------ #

    def import_legacy_queue(self, items: List[Dict[str, Any]]) -> int:
        """Import tasks previously persisted to ``gui_queue.json``."""
        count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("id") or "")
            if not tid or self.load_task(tid) is not None:
                continue
            task = {
                "id": tid,
                "url": str(item.get("url", "")),
                "filename": str(item.get("label", "") or ""),
                "directory": str(item.get("directory", "")),
                "label": str(item.get("label", "") or ""),
                "checksum": str(item.get("checksum", "") or ""),
                "total_size": int(item.get("total", 0) or 0),
                "downloaded_size": int(item.get("completed", 0) or 0),
                "status": str(item.get("state", "Queued") or "Queued"),
                "error": str(item.get("error", "") or ""),
                "created_at": float(item.get("created_at", 0) or 0) or None,
                "completed_at": item.get("finished_at"),
            }
            self.save_task(task)
            count += 1
        return count

    def import_legacy_history(self, entries: List[Dict[str, Any]]) -> int:
        count = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            self.add_history(entry)
            count += 1
        return count

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


def _human_size(value: float) -> str:
    try:
        size = max(0.0, float(value))
    except Exception:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"
