"""Schema DDL and the versioned migration ledger.

Separated from :mod:`core.db` on purpose.  ``core.db`` answers *"how do I talk to
SQLite safely?"* — connection, PRAGMAs, corruption quarantine, query surface.
This module answers *"what does the schema look like, and how does an old
database become a new one?"*.  Keeping them apart means the migration history can
grow for years without the connection layer getting any bigger, and a migration
can be read and reviewed on its own.

Rules
-----
* ``SCHEMA_VERSION`` and the tail of :data:`MIGRATIONS` are bumped **together**.
  The runner compares the constant with ``PRAGMA user_version``.
* Every migration is **idempotent**.  The ledger is advanced only after a
  migration commits, so an interrupted upgrade re-runs instead of skipping
  ahead — which means "already done" must be a safe answer.
* Migrations are append-only.  Never renumber or delete an entry: an install in
  the field has already recorded the old numbering.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import List, Sequence

# --------------------------------------------------------------------------- #
# Version
# --------------------------------------------------------------------------- #

# 1 = tasks / history / queue_order
# 2 = projects + tasks.project_id
SCHEMA_VERSION = 2

# --------------------------------------------------------------------------- #
# Identity of the implicit project
# --------------------------------------------------------------------------- #

# Fixed rather than generated so the v2 migration is idempotent: re-running it
# can never create a second default, and the id is stable in every log, URL and
# test that mentions it.
DEFAULT_PROJECT_ID = "default"
DEFAULT_PROJECT_NAME = "Default"
DEFAULT_PROJECT_DESCRIPTION = "Downloads created before projects existed."

# --------------------------------------------------------------------------- #
# DDL
# --------------------------------------------------------------------------- #

_BASELINE_STATEMENTS: Sequence[str] = (
    """
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
    )
    """,
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS queue_order (
        task_id TEXT PRIMARY KEY,
        pos     INTEGER NOT NULL DEFAULT 0
    )
    """,
)

_PROJECTS_STATEMENTS: Sequence[str] = (
    """
    CREATE TABLE IF NOT EXISTS projects (
        id                TEXT PRIMARY KEY,
        name              TEXT NOT NULL,
        description       TEXT NOT NULL DEFAULT '',
        directory         TEXT NOT NULL DEFAULT '',
        created_at        REAL NOT NULL,
        updated_at        REAL NOT NULL,
        max_concurrent    INTEGER NOT NULL DEFAULT 0,
        schedule_enabled  INTEGER NOT NULL DEFAULT 0,
        schedule_start    TEXT,
        schedule_stop     TEXT,
        schedule_days     TEXT NOT NULL DEFAULT '[]',
        completion_action TEXT NOT NULL DEFAULT 'none',
        status            TEXT NOT NULL DEFAULT 'active',
        is_default        INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name COLLATE NOCASE)",
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    """Column names of *table*, or ``[]`` when it does not exist.

    Tolerant on purpose: callers ask about a table precisely because they are
    not yet sure it is there, and ``PRAGMA table_info`` already answers "no such
    table" with an empty result rather than an error.
    """
    return [str(r[1]) for r in conn.execute("PRAGMA table_info(%s)" % table)]


def add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, ddl: str
) -> bool:
    """Add a column only when absent.  Returns True when it was added.

    A missing table yields False rather than raising, matching
    :func:`table_columns`.  Silently skipping is safe here because every caller
    either created the table earlier in the same migration or is guarding a
    legacy shape; a genuinely wrong table name still surfaces immediately, just
    from the statement that actually needed the column.
    """
    if column in table_columns(conn, table):
        return False
    if not table_columns(conn, table):
        return False
    conn.execute("ALTER TABLE %s ADD COLUMN %s" % (table, ddl))
    return True


# --------------------------------------------------------------------------- #
# Migrations
# --------------------------------------------------------------------------- #


def _migrate_to_1(conn: sqlite3.Connection) -> None:
    """Baseline schema.

    Idempotent because databases created before the ledger existed report
    ``user_version = 0`` while already holding these tables.
    """
    for statement in _BASELINE_STATEMENTS:
        conn.execute(statement)
    # Very old databases created ``history`` without this column.
    add_column_if_missing(
        conn, "history", "connection_mode", "connection_mode TEXT NOT NULL DEFAULT ''"
    )


def _migrate_to_2(conn: sqlite3.Connection) -> None:
    """Projects, plus a ``project_id`` on every task.

    Legacy tasks are attached to the Default project rather than left with an
    empty ``project_id``.  An unassociated task would be invisible in a
    project-scoped view, which is indistinguishable from data loss to the user.

    Note: ``tasks.project_id`` deliberately carries no SQL ``REFERENCES`` clause.
    SQLite's ``ALTER TABLE ... ADD COLUMN`` cannot add a foreign key with a
    non-NULL default, and rebuilding ``tasks`` to get one would mean copying
    every row — a real data-loss risk for a constraint the service layer already
    enforces inside a transaction.  See ``docs/PROJECTS.md`` §3.
    """
    for statement in _PROJECTS_STATEMENTS:
        conn.execute(statement)
    add_column_if_missing(
        conn, "tasks", "project_id", "project_id TEXT NOT NULL DEFAULT ''"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id)")

    now = datetime.now().timestamp()
    conn.execute(
        """
        INSERT INTO projects (
            id, name, description, directory, created_at, updated_at,
            max_concurrent, schedule_enabled, schedule_start, schedule_stop,
            schedule_days, completion_action, status, is_default
        ) VALUES (?, ?, ?, '', ?, ?, 0, 0, NULL, NULL, '[]', 'none', 'active', 1)
        ON CONFLICT(id) DO NOTHING
        """,
        (
            DEFAULT_PROJECT_ID,
            DEFAULT_PROJECT_NAME,
            DEFAULT_PROJECT_DESCRIPTION,
            now,
            now,
        ),
    )
    conn.execute(
        "UPDATE tasks SET project_id=? WHERE project_id IS NULL OR project_id=''",
        (DEFAULT_PROJECT_ID,),
    )


# Ordered, append-only.  Each entry is ``(version, description, callable)``.
MIGRATIONS: Sequence[tuple] = (
    (1, "baseline: tasks, history, queue_order", _migrate_to_1),
    (2, "projects table and tasks.project_id", _migrate_to_2),
)
