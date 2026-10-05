"""Phase 1 — Duplicate Download Detection (queue-level focused tests)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.session import SessionManager
from core.task import TaskStatus
from ui.api import Api
from ui.common import DownloadRequest, TaskManager
from ui.legacy import LegacyDownloadRunner


def _mgr(dirpath, cfg=None):
    cfg = cfg or AppConfig()
    return TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                       dirpath, max_concurrent=2, config=cfg)


class DuplicateDetectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AppConfig()
        self.cfg.block_private_urls = False
        self.cfg.connection_mode = "manual"

    def _manager(self):
        return _mgr(Path(tempfile.mkdtemp()), self.cfg)

    def test_level1_active_url(self):
        m = self._manager()
        t1 = m.add(DownloadRequest(url="https://a.example/file.zip", directory=str(self.tmp)),
                   autostart=False)
        c = m.check_duplicate("https://a.example/file.zip", str(self.tmp), "file.zip")
        self.assertTrue(c["has_active"])
        self.assertEqual(c["active_task_id"], t1)
        self.assertEqual(c["reason"], "same_url")
        # A different URL -> no conflict.
        c2 = m.check_duplicate("https://b.example/other.zip", str(self.tmp), "other.zip")
        self.assertFalse(c2["has_active"])
        m.close()

    def test_level2_history(self):
        m = self._manager()
        # A completed task (terminal) so Level 1 does not shadow history.
        t = m.add(DownloadRequest(url="https://a.example/file.zip", directory=str(self.tmp)),
                  autostart=False)
        rec = m._tasks[t]
        rec.task.force_status(TaskStatus.COMPLETED)
        rec.task.downloaded_size = 100
        rec.task.total_size = 100
        m._add_history_locked(rec, rec.task)
        m.remove_task(t)   # drop the active record, keep history
        c = m.check_duplicate("https://a.example/file.zip", str(self.tmp), "")
        self.assertTrue(c["in_history"])
        self.assertEqual(c["history_count"], 1)
        m.close()

    def test_level3_file_exists(self):
        m = self._manager()
        target = self.tmp / "movie.mp4"
        target.write_bytes(b"existing movie")
        c = m.check_duplicate("https://a.example/movie.mp4", str(self.tmp), "movie.mp4")
        self.assertTrue(c["file_exists"])
        self.assertEqual(c["file_path"], str(target))
        self.assertEqual(c["reason"], "file_exists")
        m.close()

    def test_no_conflict(self):
        m = self._manager()
        c = m.check_duplicate("https://a.example/new.bin", str(self.tmp), "new.bin")
        self.assertFalse(c["has_active"] or c["in_history"] or c["file_exists"])
        self.assertEqual(c["reason"], "")
        m.close()

    def test_add_dedupe_returns_existing(self):
        m = self._manager()
        t1 = m.add(DownloadRequest(url="https://a.example/f.zip", directory=str(self.tmp)),
                   autostart=False)
        t2 = m.add(DownloadRequest(url="https://a.example/f.zip", directory=str(self.tmp)),
                   autostart=False)
        self.assertEqual(t1, t2)          # same task returned
        self.assertEqual(len(m.snapshots()), 1)
        m.close()

    def test_manual_override_allow_duplicate(self):
        m = self._manager()
        t1 = m.add(DownloadRequest(url="https://a.example/f.zip", directory=str(self.tmp)),
                   autostart=False)
        t2 = m.add(DownloadRequest(url="https://a.example/f.zip", directory=str(self.tmp)),
                   autostart=False, allow_duplicate=True)
        self.assertNotEqual(t1, t2)       # intentional second download
        self.assertEqual(len(m.snapshots()), 2)
        m.close()


class ApiDuplicateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AppConfig()
        self.cfg.block_private_urls = False
        self.cfg.connection_mode = "manual"
        # Isolate the Api's task store from the real user data directory so
        # these queued test tasks never persist into %LOCALAPPDATA%/N13.
        from unittest.mock import patch

        import core.paths

        self._data_dir_patch = patch.object(
            core.paths, "data_dir", return_value=self.tmp / "data"
        )
        self._data_dir_patch.start()
        self.addCleanup(self._data_dir_patch.stop)
        self.api = Api(self.cfg, SessionManager(self.cfg))

    def tearDown(self):
        self.api.shutdown()

    def test_api_check_duplicate_file(self):
        target = self.tmp / "file.bin"
        target.write_bytes(b"x" * 10)
        c = self.api.check_duplicate("https://a.example/file.bin", str(self.tmp), "file.bin")
        self.assertTrue(c["file_exists"])

    def test_api_replace_resolves_conflict(self):
        target = self.tmp / "replace.bin"
        target.write_bytes(b"old")
        tid = self.api.add_download("https://a.example/replace.bin", directory=str(self.tmp),
                                    label="replace.bin", autostart=False,
                                    resolve_conflict="replace")
        self.assertTrue(tid)
        self.assertFalse(target.exists(), "Replace policy should delete the old file")

    def test_api_allow_duplicate(self):
        t1 = self.api.add_download("https://a.example/dup.zip", directory=str(self.tmp),
                                   label="dup.zip", autostart=False)
        t2 = self.api.add_download("https://a.example/dup.zip", directory=str(self.tmp),
                                   label="dup.zip", autostart=False, allow_duplicate=True)
        self.assertNotEqual(t1, t2)

    def test_batch_policy_allow(self):
        self.cfg.duplicate_policy = "allow"
        n = self.api.add_batch(["https://a.example/a.zip", "https://a.example/a.zip"], str(self.tmp))
        self.assertEqual(n, 2)

    def test_batch_policy_replace(self):
        self.cfg.duplicate_policy = "replace"
        target = self.tmp / "a.zip"
        target.write_bytes(b"old")
        self.api.add_batch(["https://a.example/a.zip"], str(self.tmp))
        self.assertFalse(target.exists(), "replace policy should delete the file")


if __name__ == "__main__":
    unittest.main()
