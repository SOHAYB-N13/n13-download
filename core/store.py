"""Task and history repository (SQLite).

A thin repository over :class:`core.db.Database`.  The connection, the PRAGMAs,
the corruption quarantine and the versioned migrations live in ``core/db.py`` so
that a second domain (projects) can share the same database file without either
duplicating the connection handling or growing this module into a god class.

Scope
-----
This module owns **the ``tasks`` and ``history`` tables and nothing else**.  It
does not decide policy: it does not know what a valid status is, which task
should run next, or what a project is allowed to do.  ``ui/common.py::
TaskManager`` and ``projects/service.py`` make those decisions and call here to
persist the outcome.

Composition
-----------
Several methods accept an optional ``conn``.  ``ProjectService`` needs to delete
a project row *and* its task rows in one transaction, and it must not write SQL
itself, so the repository methods can be handed the connection that the service
opened via :meth:`core.db.Database.transaction`.  Passing ``None`` keeps the
normal self-committing behaviour.

Why one class and not three
---------------------------
This module is ~450 lines, above the 350-line comfort target.  It stays a single
class on purpose: ``tasks``, ``history`` and ``queue_order`` are one aggregate —
every write path touches at least two of them (finishing a task writes the task
row, appends history and prunes the order), and splitting them would either
duplicate the transaction handling or introduce a coordinator with no behaviour
of its own.  The split that *did* pay off was moving the connection, the PRAGMAs,
the quarantine and the migration ledger into ``core/db.py``, which is why this
file is a repository rather than a 900-line storage class.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from core.db import DEFAULT_PROJECT_ID, Database
from core.utils import human_size

# Re-exported: ``tests/test_store.py`` imports the private name from here, and it
# has been part of this module's surface since the corruption handling landed.
from core.db import is_corruption_error as _is_corruption_error  # noqa: F401

__all__ = ["TaskStore"]

# Every ``tasks`` column ``save_task`` writes, declared once.  The INSERT and the
# ON CONFLICT update clause are generated from this tuple, so a new column is a
# one-line change and the two halves can never drift apart.
_TASK_COLUMNS = (
    "id",
    "url",
    "filename",
    "directory",
    "label",
    "total_size",
    "downloaded_size",
    "current_speed",
    "average_speed",
    "eta_seconds",
    "status",
    "priority",
    "created_at",
    "started_at",
    "completed_at",
    "retry_count",
    "error",
    "connections",
    "checksum",
    "content_type",
    "server",
    "supports_range",
    "etag",
    "last_modified",
    "category",
    "autostart",
    "speed_limit_bps",
    "segments_json",
    "project_id",
)

_INSERT_TASK_SQL = "INSERT INTO tasks ({cols}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET {sets}".format(
    cols=", ".join(_TASK_COLUMNS),
    marks=", ".join("?" for _ in _TASK_COLUMNS),
    sets=", ".join("%s=excluded.%s" % (c, c) for c in _TASK_COLUMNS),
)


class TaskStore:
    """Thread-safe SQLite repository for download tasks and history."""

    def __init__(self, db_path: Union[Path, str, Database]):
        if isinstance(db_path, Database):
            self._db = db_path
        else:
            self._db = Database(Path(db_path))

    # ------------------------------------------------------------------ #
    # Handle plumbing
    # ------------------------------------------------------------------ #

    @property
    def db(self) -> Database:
        """The shared connection holder, so services can reuse one handle."""
        return self._db

    @property
    def _conn(self) -> sqlite3.Connection:
        """The raw connection.

        Kept as a read-through property rather than a cached attribute: the
        connection object is replaced when a corrupt database is quarantined, and
        a stale copy would silently point at a closed handle.  ``tests/
        test_store.py`` reads this attribute directly.
        """
        return self._db._conn

    @property
    def _lock(self):
        return self._db.lock

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._db.execute(sql, params)

    def _query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        return self._db.query(sql, params)

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        return self._db.query_one(sql, params)

    # ------------------------------------------------------------------ #
    # Serialisation helpers
    # ------------------------------------------------------------------ #

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

    @staticmethod
    def _task_values(task: Dict[str, Any], segments_json: str) -> tuple:
        """Positional values matching :data:`_TASK_COLUMNS`, in order."""
        return (
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
            segments_json,
            # A task always belongs to exactly one project.  Callers that predate
            # the project layer (and the legacy JSON import) leave this empty;
            # normalising here keeps the same invariant the v2 migration applied
            # to existing rows, so the two paths cannot diverge.
            str(task.get("project_id") or "") or DEFAULT_PROJECT_ID,
        )

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
        self._execute(_INSERT_TASK_SQL, self._task_values(task, seg_json))

    def load_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._query_one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def list_tasks(self) -> List[Dict[str, Any]]:
        return self._query("SELECT * FROM tasks ORDER BY created_at")

    def delete_task(self, task_id: str) -> None:
        self._execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self._execute("DELETE FROM queue_order WHERE task_id=?", (task_id,))

    def update_progress(self, task_id: str, downloaded: int, total: int) -> None:
        """Low-cost progress write used between transitions (coalesced by caller)."""
        self._execute(
            "UPDATE tasks SET downloaded_size=?, total_size=? WHERE id=?",
            (max(0, int(downloaded)), max(0, int(total)), task_id),
        )

    def clear_finished_tasks(self) -> int:
        """Delete terminal tasks (Complete/Failed/Cancelled); return count."""
        cur = self._execute(
            "DELETE FROM tasks WHERE status IN ('Complete','Failed','Cancelled')"
        )
        return int(getattr(cur, "rowcount", 0) or 0)

    def _segments_for(self, task_id: str) -> List[Dict[str, Any]]:
        row = self.load_task(task_id)
        if not row:
            return []
        return self._json_to_segments(row.get("segments_json", "[]"))

    # ------------------------------------------------------------------ #
    # Project-scoped task access
    # ------------------------------------------------------------------ #
    #
    # The ``tasks`` table has exactly one writer (this class).  ``projects/``
    # never issues SQL against it — it asks here — which is what keeps the two
    # domains from growing parallel, subtly different task queries.

    def list_tasks_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        return self._query(
            "SELECT * FROM tasks WHERE project_id=? ORDER BY created_at", (project_id,)
        )

    def task_ids_for_project(self, project_id: str) -> List[str]:
        rows = self._query("SELECT id FROM tasks WHERE project_id=?", (project_id,))
        return [str(r.get("id") or "") for r in rows]

    def aggregate_by_project(
        self, conn: Optional[sqlite3.Connection] = None
    ) -> List[Dict[str, Any]]:
        """One row per ``(project, status)`` with counts and byte totals.

        A single grouped query rather than one query per project: the project
        dashboard needs counts *and* progress for every project at once, and an
        N+1 pattern here would show up as a visible stall on the Projects page.
        """
        sql = (
            "SELECT project_id, status, COUNT(*) AS n, "
            "COALESCE(SUM(downloaded_size), 0) AS done, "
            "COALESCE(SUM(total_size), 0) AS total "
            "FROM tasks GROUP BY project_id, status"
        )
        if conn is not None:
            return [dict(r) for r in conn.execute(sql).fetchall()]
        return self._query(sql)

    def has_unfinished_tasks(
        self, project_id: str, conn: Optional[sqlite3.Connection] = None
    ) -> bool:
        """True when the project still owns work that is not in a terminal state.

        ``Removed`` counts as finished (the user removed it deliberately), which
        matches ``AutoShutdownPolicy``'s definition of finished work.
        """
        sql = (
            "SELECT COUNT(*) AS n FROM tasks WHERE project_id=? "
            "AND status NOT IN ('Complete','Failed','Cancelled','Removed')"
        )
        if conn is not None:
            row = conn.execute(sql, (project_id,)).fetchone()
            return int(row[0] if row else 0) > 0
        rows = self._query(sql, (project_id,))
        return int(rows[0].get("n") or 0) > 0 if rows else False

    def reassign_project(
        self, from_project: str, to_project: str, conn: Optional[sqlite3.Connection] = None
    ) -> int:
        """Move every task of one project to another.  Returns rows touched."""
        sql = "UPDATE tasks SET project_id=? WHERE project_id=?"
        params = (to_project, from_project)
        if conn is not None:
            return int(conn.execute(sql, params).rowcount)
        return int(getattr(self._execute(sql, params), "rowcount", 0) or 0)

    def delete_tasks_by_project(
        self, project_id: str, conn: Optional[sqlite3.Connection] = None
    ) -> int:
        """Delete every task row of a project, plus their queue-order entries.

        The caller supplies ``conn`` when this must be atomic with the project
        row deletion; otherwise the statements commit on their own.
        """
        count_sql = "SELECT COUNT(*) AS n FROM tasks WHERE project_id=?"
        order_sql = "DELETE FROM queue_order WHERE task_id IN (SELECT id FROM tasks WHERE project_id=?)"
        task_sql = "DELETE FROM tasks WHERE project_id=?"

        if conn is not None:
            row = conn.execute(count_sql, (project_id,)).fetchone()
            count = int(row[0] if row else 0)
            conn.execute(order_sql, (project_id,))
            conn.execute(task_sql, (project_id,))
            return count

        rows = self._query(count_sql, (project_id,))
        count = int(rows[0].get("n") or 0) if rows else 0
        with self._db.transaction() as tx:
            tx.execute(order_sql, (project_id,))
            tx.execute(task_sql, (project_id,))
        return count

    # ------------------------------------------------------------------ #
    # Queue order
    # ------------------------------------------------------------------ #

    def save_order(self, task_ids: List[str]) -> None:
        """Persist the explicit queue ordering (move up/down)."""
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM queue_order")
            conn.executemany(
                "INSERT INTO queue_order (task_id, pos) VALUES (?, ?)",
                [(tid, i) for i, tid in enumerate(task_ids)],
            )

    def load_order(self) -> List[str]:
        rows = self._query("SELECT task_id, pos FROM queue_order ORDER BY pos")
        return [r["task_id"] for r in rows]

    def prune_order(self, live_task_ids: Any) -> int:
        """Drop queue-order rows whose task no longer exists.  Returns count."""
        keep = set(live_task_ids)
        stale = [tid for tid in self.load_order() if tid not in keep]
        if not stale:
            return 0
        with self._db.transaction() as conn:
            conn.executemany("DELETE FROM queue_order WHERE task_id=?", [(t,) for t in stale])
        return len(stale)

    # ------------------------------------------------------------------ #
    # History
    # ------------------------------------------------------------------ #

    def add_history(self, entry: Dict[str, Any]) -> None:
        with self._db.transaction() as conn:
            conn.execute(
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
            conn.execute(
                "DELETE FROM history WHERE id NOT IN "
                "(SELECT id FROM history ORDER BY id DESC LIMIT 1000)"
            )

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
                    "size": human_size(int(r.get("size_bytes", 0) or 0)),
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
    # Legacy JSON import
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

    # ------------------------------------------------------------------ #

    def close(self) -> None:
        self._db.close()
