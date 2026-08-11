"""Phase C — SQLite store tests: persistence, concurrency, corruption, migration."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.store import TaskStore
from core.task import DownloadTask, TaskStatus


def _task(i: int = 0, status="Queued") -> dict:
    return DownloadTask(
        url=f"https://x/{i}.zip",
        directory="C:/dl",
        filename=f"{i}.zip",
        category="Archives",
        status=status,
        total_size=1000,
        downloaded_size=400,
    ).to_dict()


class StoreBasicsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.store = TaskStore(self.dir / "t.db")

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_insert_update_delete(self):
        t = _task()
        self.store.save_task(t)
        row = self.store.load_task(t["id"])
        self.assertEqual(row["url"], t["url"])
        self.assertEqual(row["status"], "Queued")

        t2 = dict(t)
        t2["status"] = TaskStatus.DOWNLOADING.value
        t2["downloaded_size"] = 999
        self.store.save_task(t2)
        row = self.store.load_task(t["id"])
        self.assertEqual(row["status"], "Downloading")
        self.assertEqual(row["downloaded_size"], 999)

        self.store.delete_task(t["id"])
        self.assertIsNone(self.store.load_task(t["id"]))

    def test_schema_pragma(self):
        conn = self.store._conn
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(v, 1)

    def test_history_bounded(self):
        for i in range(1050):
            self.store.add_history({
                "task_id": f"t{i}", "name": f"f{i}.zip", "url": "u",
                "directory": "d", "category": "General", "size_bytes": 1,
                "status": "Complete", "duration": 1, "avg_speed": 1,
                "finished": "x",
            })
        hist = self.store.list_history(1000)
        self.assertLessEqual(len(hist), 1000)
        self.assertEqual(hist[0]["name"], "f1049.zip")

    def test_segments_roundtrip(self):
        segs = [{"index": 0, "start": 0, "end": 499, "path": "C:/dl/x.part0", "done": 1}]
        t = _task()
        t["segments"] = segs
        self.store.save_task(t)
        row = self.store.load_task(t["id"])
        self.assertEqual(self.store._segments_for(t["id"]), segs)

    def test_import_legacy(self):
        n = self.store.import_legacy_queue([
            {"id": "L1", "url": "https://a/b.zip", "directory": "C:/x",
             "state": "Downloading", "total": 100, "completed": 10},
        ])
        self.assertEqual(n, 1)
        row = self.store.load_task("L1")
        self.assertEqual(row["status"], "Downloading")
        self.assertEqual(row["total_size"], 100)

        self.store.import_legacy_history([
            {"name": "h", "url": "u", "directory": "d", "size": "1 MB",
             "size_bytes": 1000, "status": "Complete", "finished": "now"},
        ])
        hist = self.store.list_history()
        self.assertEqual(hist[0]["name"], "h")


class StoreConcurrencyTest(unittest.TestCase):
    def test_parallel_writes_no_locking_errors(self):
        d = Path(tempfile.mkdtemp())
        store = TaskStore(d / "c.db")
        errors = []
        lock = threading.Lock()

        def writer(n):
            try:
                for i in range(60):
                    store.save_task(_task(n * 100 + i))
                    store.add_history({"task_id": "x", "name": f"{n}-{i}", "url": "u",
                                      "directory": "d", "category": "G", "size_bytes": 1,
                                      "status": "S", "duration": 0, "avg_speed": 0,
                                      "finished": "t"})
            except Exception as exc:  # pragma: no cover
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(store.list_tasks()), 240)
        store.close()

    def test_corrupt_db_recovered(self):
        """A corrupt database must be quarantined and rebuilt, not fatal."""
        d = Path(tempfile.mkdtemp())
        db = d / "bad.db"
        db.write_bytes(b"this is not a sqlite database at all........")
        store = TaskStore(db)
        # A fresh, usable database exists in place of the corrupt one.
        self.assertTrue(db.exists())
        store.save_task(_task(1))
        task_id = store.list_tasks()[0]["id"]
        self.assertIsNotNone(store.load_task(task_id))
        store.close()
        # The corrupt original is preserved for diagnostics.
        corrupts = [p for p in d.iterdir() if "corrupt" in p.name]
        self.assertTrue(corrupts, "corrupt database was not preserved")

    def test_corruption_classification(self):
        """Non-corruption runtime errors must NOT be treated as corruption."""
        from core.store import _is_corruption_error
        import sqlite3

        self.assertTrue(_is_corruption_error(sqlite3.DatabaseError("file is not a database")))
        self.assertTrue(_is_corruption_error(sqlite3.DatabaseError("database disk image is malformed")))
        # Ordinary runtime conditions — never corruption:
        self.assertFalse(_is_corruption_error(sqlite3.OperationalError("database is locked")))
        self.assertFalse(_is_corruption_error(sqlite3.OperationalError("database or disk is full")))
        self.assertFalse(_is_corruption_error(sqlite3.OperationalError("unable to open database file")))
        self.assertFalse(_is_corruption_error(sqlite3.OperationalError("attempt to write a readonly database")))

    def test_readonly_database_not_quarantined(self):
        """A read-only (permission) failure is operational, not corruption."""
        import os
        import sqlite3
        import stat

        d = Path(tempfile.mkdtemp())
        db = d / "ro.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t(x)")
        conn.commit()
        conn.close()
        os.chmod(db, stat.S_IREAD)  # read-only attribute (works on Windows NTFS)
        try:
            with self.assertRaises((sqlite3.OperationalError, sqlite3.DatabaseError, PermissionError)):
                TaskStore(db)
            # The valid file must NOT have been quarantined/renamed.
            self.assertFalse(any("corrupt" in p.name for p in d.iterdir()))
        finally:
            os.chmod(db, stat.S_IWRITE)  # restore so tempdir cleanup works

    def test_interrupted_initialization_recovers(self):
        """A database truncated mid-initialization is corruption -> recover."""
        d = Path(tempfile.mkdtemp())
        db = d / "interrupted.db"
        # A valid header followed by garbage (a partially written database).
        db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4090)
        store = TaskStore(db)  # must recover, not crash
        self.assertTrue(db.exists())
        store.save_task(_task(2))
        self.assertTrue(store.list_tasks())
        store.close()
        self.assertTrue(any("corrupt" in p.name for p in d.iterdir()))

    def test_two_stores_same_file_wal(self):
        d = Path(tempfile.mkdtemp())
        a = TaskStore(d / "s.db")
        b = TaskStore(d / "s.db")
        a.save_task(_task(0))
        b.save_task(_task(1))
        self.assertIsNotNone(a.load_task("x0" if False else a.list_tasks()[0]["id"]))
        self.assertEqual(len(b.list_tasks()), 2)
        a.close()
        b.close()


class StorePersistenceTest(unittest.TestCase):
    def test_state_survives_reopen(self):
        d = Path(tempfile.mkdtemp())
        db = d / "p.db"
        s = TaskStore(db)
        t = _task(5)
        t["status"] = TaskStatus.PAUSED.value
        s.save_task(t)
        s.close()

        s2 = TaskStore(db)
        row = s2.load_task(t["id"])
        self.assertEqual(row["status"], "Paused")
        s2.close()

    def test_order_persistence(self):
        d = Path(tempfile.mkdtemp())
        s = TaskStore(d / "o.db")
        s.save_order(["b", "a", "c"])
        self.assertEqual(s.load_order(), ["b", "a", "c"])
        s.close()


if __name__ == "__main__":
    unittest.main()
