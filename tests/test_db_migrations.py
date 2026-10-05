"""Project layer — schema migration ledger tests.

``core/migrations.py`` is the only place that knows how an old database becomes
a new one, and a mistake there is the one class of bug that destroys a user's
data instead of merely annoying them.  These tests therefore exercise the real
ledger (``MIGRATIONS``) rather than a hand-rolled copy of the DDL, and they build
the "old" database by *applying the old migration* — so if migration 1 is ever
edited, the v1 fixture changes with it and the v1→v2 test still means something.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import Database, is_corruption_error  # noqa: F401  (re-export guard)
from core.migrations import (
    DEFAULT_PROJECT_DESCRIPTION,
    DEFAULT_PROJECT_ID,
    DEFAULT_PROJECT_NAME,
    MIGRATIONS,
    SCHEMA_VERSION,
    add_column_if_missing,
    table_columns,
)


def _apply_through(conn: sqlite3.Connection, version: int) -> None:
    """Replay the ledger up to and including *version* on a raw connection."""
    for v, _description, migrate in MIGRATIONS:
        if v > version:
            break
        migrate(conn)
    conn.execute("PRAGMA user_version=%d" % int(version))
    conn.commit()


class _TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "n13.db"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _open(self) -> Database:
        return Database(self.path)

    def _raw(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path))


class FreshDatabaseTest(_TempDb):
    def test_new_database_reaches_the_current_schema_version(self):
        db = self._open()
        try:
            self.assertEqual(db.schema_version(), SCHEMA_VERSION)
        finally:
            db.close()

    def test_every_ledger_entry_is_ordered_and_unique(self):
        versions = [v for v, _d, _fn in MIGRATIONS]
        self.assertEqual(versions, sorted(versions), "migrations must stay in order")
        self.assertEqual(len(versions), len(set(versions)), "duplicate ledger version")
        self.assertEqual(versions[-1], SCHEMA_VERSION, "SCHEMA_VERSION must match the ledger tail")

    def test_baseline_tables_exist(self):
        db = self._open()
        try:
            names = {
                row["name"]
                for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            db.close()
        for table in ("tasks", "history", "queue_order", "projects"):
            self.assertIn(table, names)

    def test_running_migrations_twice_is_a_no_op(self):
        db = self._open()
        try:
            self.assertEqual(db.run_migrations(), 0)
            self.assertEqual(db.run_migrations(), 0)
            self.assertEqual(db.schema_version(), SCHEMA_VERSION)
        finally:
            db.close()

    def test_default_project_is_created_exactly_once(self):
        db = self._open()
        try:
            # Re-running the whole ledger must not duplicate the default row.
            db.run_migrations()
            rows = db.query("SELECT id, name, is_default FROM projects")
        finally:
            db.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], DEFAULT_PROJECT_ID)
        self.assertEqual(rows[0]["name"], DEFAULT_PROJECT_NAME)
        self.assertEqual(rows[0]["is_default"], 1)

    def test_tasks_table_carries_project_id(self):
        db = self._open()
        try:
            columns = table_columns(db._conn, "tasks")
        finally:
            db.close()
        self.assertIn("project_id", columns)


class LegacyUpgradeTest(_TempDb):
    """A database written before projects existed must upgrade in place."""

    def _make_v1(self) -> None:
        conn = self._raw()
        try:
            _apply_through(conn, 1)
            # A v1 `tasks` table has no `project_id` at all — that is the whole
            # point of migration 2, so the fixture must not provide one.
            self.assertNotIn("project_id", table_columns(conn, "tasks"))
            for task_id, name in (("legacy-1", "old.zip"), ("legacy-2", "blank.zip")):
                conn.execute(
                    "INSERT INTO tasks (id, url, filename, directory, created_at) "
                    "VALUES (?, ?, ?, 'C:/dl', 1.0)",
                    (task_id, f"https://x/{name}", name),
                )
            conn.execute(
                "INSERT INTO history (task_id, name, url, status) "
                "VALUES ('legacy-1', 'old.zip', 'u', 'Complete')"
            )
            conn.commit()
        finally:
            conn.close()

    def test_v1_database_is_upgraded_to_the_current_version(self):
        self._make_v1()
        db = self._open()
        try:
            self.assertEqual(db.schema_version(), SCHEMA_VERSION)
        finally:
            db.close()

    def test_legacy_tasks_are_attached_to_the_default_project(self):
        """An unassociated task would be invisible in a project view — i.e. lost."""
        self._make_v1()
        db = self._open()
        try:
            rows = db.query("SELECT id, project_id FROM tasks ORDER BY id")
        finally:
            db.close()
        self.assertEqual([r["id"] for r in rows], ["legacy-1", "legacy-2"])
        for row in rows:
            self.assertEqual(row["project_id"], DEFAULT_PROJECT_ID)

    def test_default_project_describes_where_it_came_from(self):
        self._make_v1()
        db = self._open()
        try:
            row = db.query_one(
                "SELECT description, is_default FROM projects WHERE id=?",
                (DEFAULT_PROJECT_ID,),
            )
        finally:
            db.close()
        self.assertEqual(row["description"], DEFAULT_PROJECT_DESCRIPTION)
        self.assertEqual(row["is_default"], 1)

    def test_upgrade_preserves_history_rows(self):
        self._make_v1()
        db = self._open()
        try:
            rows = db.query("SELECT task_id, name FROM history")
        finally:
            db.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_id"], "legacy-1")

    def test_upgrade_does_not_duplicate_the_default_project(self):
        """A v1 db that already ran migration 2 once must not gain a second row."""
        self._make_v1()
        db = self._open()
        db.close()
        # Second open: migrations are already at the head, nothing should run.
        db = self._open()
        try:
            count = db.query_one("SELECT COUNT(*) AS n FROM projects")["n"]
        finally:
            db.close()
        self.assertEqual(count, 1)

    def test_task_created_before_the_column_existed_keeps_its_data(self):
        self._make_v1()
        db = self._open()
        try:
            row = db.query_one("SELECT url, filename, directory FROM tasks WHERE id='legacy-1'")
        finally:
            db.close()
        self.assertEqual(row["url"], "https://x/old.zip")
        self.assertEqual(row["filename"], "old.zip")
        self.assertEqual(row["directory"], "C:/dl")


class HelperTest(_TempDb):
    def test_table_columns_reports_real_columns(self):
        db = self._open()
        try:
            columns = table_columns(db._conn, "tasks")
        finally:
            db.close()
        self.assertIn("id", columns)
        self.assertIn("url", columns)

    def test_table_columns_of_a_missing_table_is_empty(self):
        db = self._open()
        try:
            self.assertEqual(table_columns(db._conn, "no_such_table"), [])
        finally:
            db.close()

    def test_add_column_if_missing_is_idempotent(self):
        db = self._open()
        try:
            self.assertTrue(add_column_if_missing(db._conn, "tasks", "probe_col", "probe_col TEXT DEFAULT ''"))
            self.assertFalse(add_column_if_missing(db._conn, "tasks", "probe_col", "probe_col TEXT DEFAULT ''"))
            self.assertIn("probe_col", table_columns(db._conn, "tasks"))
        finally:
            db.close()

    def test_add_column_if_missing_ignores_a_missing_table(self):
        db = self._open()
        try:
            self.assertFalse(add_column_if_missing(db._conn, "ghost", "x", "x TEXT"))
        finally:
            db.close()


class FutureVersionTest(_TempDb):
    def test_a_newer_schema_version_is_left_alone(self):
        """A database written by a newer build must not be downgraded.

        The ledger only ever moves forward, so opening a future file applies
        nothing and leaves the recorded version intact.
        """
        conn = self._raw()
        try:
            _apply_through(conn, SCHEMA_VERSION)
            conn.execute("PRAGMA user_version=%d" % (SCHEMA_VERSION + 5))
            conn.commit()
        finally:
            conn.close()

        db = self._open()
        try:
            self.assertEqual(db.schema_version(), SCHEMA_VERSION + 5)
            self.assertEqual(db.run_migrations(), 0)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
