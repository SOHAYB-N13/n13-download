"""SQL for the ``projects`` table — and only that table.

One table, one writer.  The task side of a project lives in
:class:`core.store.TaskStore`; this repository never issues SQL against
``tasks``.  That split is what stops two modules from growing parallel, subtly
different ideas of what a project's task set is.

Every mutating method accepts an optional ``conn``.  ``ProjectService`` needs to
delete a project row *and* its task rows atomically, and it must not write SQL
itself — so it opens a transaction on :class:`core.db.Database` and hands the
connection down to the repositories involved.  Passing ``None`` keeps the normal
self-committing behaviour.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Dict, List, Optional

from core.db import Database
from projects.models import Project, ProjectNotFound

# Declared explicitly rather than derived from _MUTABLE_COLUMNS: an INSERT also
# carries the immutable identity columns, and a slice-based derivation is exactly
# the kind of cleverness that silently duplicates or drops a column.
_INSERT_COLUMNS = (
    "id",
    "name",
    "description",
    "directory",
    "created_at",
    "updated_at",
    "max_concurrent",
    "schedule_enabled",
    "schedule_start",
    "schedule_stop",
    "schedule_days",
    "completion_action",
    "status",
    "is_default",
)

# Immutable columns (id / created_at / is_default) are deliberately absent from
# the UPDATE statement: they are identity, not state.
_MUTABLE_COLUMNS = (
    "name",
    "description",
    "directory",
    "updated_at",
    "max_concurrent",
    "schedule_enabled",
    "schedule_start",
    "schedule_stop",
    "schedule_days",
    "completion_action",
    "status",
)

_INSERT_SQL = "INSERT INTO projects ({cols}) VALUES ({marks})".format(
    cols=", ".join(_INSERT_COLUMNS),
    marks=", ".join("?" for _ in _INSERT_COLUMNS),
)

_UPDATE_SQL = "UPDATE projects SET {sets} WHERE id=?".format(
    sets=", ".join("%s=?" % c for c in _MUTABLE_COLUMNS)
)

# Default first, then oldest first.  The Default project is the fallback owner
# of every legacy task, so pinning it at the top means the user can always see
# where an unassigned download went.
#
# Creation order — not `updated_at` — because this list drives the group tab
# strip inside the Downloads page, and a tab that reorders itself every time a
# group is renamed or paused is a tab the user will click by mistake.
_ORDER_BY = "ORDER BY is_default DESC, created_at ASC, name ASC"


class ProjectRepository:
    """Persistence for :class:`projects.models.Project`."""

    def __init__(self, db: Database):
        self._db = db

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def list_all(self) -> List[Project]:
        rows = self._db.query("SELECT * FROM projects " + _ORDER_BY)
        return [Project.from_row(row) for row in rows]

    def get(self, project_id: str) -> Optional[Project]:
        row = self._db.query_one("SELECT * FROM projects WHERE id=?", (project_id,))
        return Project.from_row(row) if row else None

    def require(self, project_id: str) -> Project:
        project = self.get(project_id)
        if project is None:
            raise ProjectNotFound(project_id)
        return project

    def exists(self, project_id: str) -> bool:
        return self._db.query_one("SELECT 1 AS x FROM projects WHERE id=?", (project_id,)) is not None

    def count(self) -> int:
        row = self._db.query_one("SELECT COUNT(*) AS n FROM projects")
        return int(row.get("n") or 0) if row else 0

    def names_excluding(self, project_id: str = "") -> List[str]:
        """Every other project's name, for the uniqueness check."""
        rows = self._db.query("SELECT name FROM projects WHERE id<>?", (project_id or "",))
        return [str(r.get("name") or "") for r in rows]

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    def insert(self, project: Project, conn: Optional[sqlite3.Connection] = None) -> None:
        row = project.to_row()
        params = tuple(row.get(col) for col in _INSERT_COLUMNS)
        if conn is not None:
            conn.execute(_INSERT_SQL, params)
            return
        self._db.execute(_INSERT_SQL, params)

    def update(self, project: Project, conn: Optional[sqlite3.Connection] = None) -> None:
        row = project.to_row()
        params = tuple(row.get(col) for col in _MUTABLE_COLUMNS) + (project.id,)
        if conn is not None:
            conn.execute(_UPDATE_SQL, params)
            return
        self._db.execute(_UPDATE_SQL, params)

    def delete(self, project_id: str, conn: Optional[sqlite3.Connection] = None) -> int:
        sql = "DELETE FROM projects WHERE id=?"
        if conn is not None:
            return int(conn.execute(sql, (project_id,)).rowcount)
        return int(getattr(self._db.execute(sql, (project_id,)), "rowcount", 0) or 0)

    def touch(self, project_id: str, when: Optional[float] = None) -> None:
        """Bump ``updated_at`` without rewriting the whole row.

        Called whenever the project's *contents* change (a task added or removed),
        so the Projects page can sort by recent activity without the caller having
        to load and re-save the project.
        """
        self._db.execute(
            "UPDATE projects SET updated_at=? WHERE id=?",
            (float(when if when is not None else time.time()), project_id),
        )

    def counts_by_status(self) -> Dict[str, int]:
        """``{status: n}`` — used by the console menu and diagnostics."""
        rows = self._db.query("SELECT status, COUNT(*) AS n FROM projects GROUP BY status")
        return {str(r.get("status") or ""): int(r.get("n") or 0) for r in rows}
