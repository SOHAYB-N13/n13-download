"""Full-path integration: TaskManager + LegacyDownloadRunner + real engine + HTTP.

This exercises the exact path the GUI uses (Api.add_download → TaskManager →
LegacyDownloadRunner → DownloadController) against a real local server,
including a true subprocess crash + restart + resume.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.session import SessionManager
from core.task import TaskStatus
from tests.helpers import DATA, RangeHandler, TestServer
from ui.common import DownloadRequest, TaskManager
from ui.legacy import LegacyDownloadRunner


def wait_until(cond, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AppConfig()
        self.cfg.block_private_urls = False
        self.cfg.connection_mode = "manual"
        self.cfg.num_threads = 4
        self.cfg.verify_size = True
        self.cfg.retry_delay = 0.01
        self.cfg.retry_max_delay = 0.2

    def _m(self, maxc=2):
        runner = LegacyDownloadRunner(self.cfg, SessionManager(self.cfg), log=lambda *a, **k: None)
        return TaskManager(runner, self.tmp, max_concurrent=maxc, config=self.cfg)

    def _clean_close(self, m):
        try:
            m.prepare_for_exit()
        finally:
            m.close()

    def test_gui_queue_real_download(self):
        with TestServer(RangeHandler) as srv:
            m = self._m(maxc=2)
            dir1, dir2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
            t1 = m.add(DownloadRequest(url=srv.url, directory=str(dir1), label="one.bin"), autostart=True)
            t2 = m.add(DownloadRequest(url=srv.url, directory=str(dir2), label="two.bin"), autostart=True)
            self.assertTrue(wait_until(lambda: all(
                m.get(t).state == TaskStatus.COMPLETED for t in (t1, t2))))
            # Files are saved under the *analyzed* server filename.
            self.assertEqual((dir1 / "file.bin").stat().st_size, len(DATA))
            self.assertEqual((dir2 / "file.bin").stat().st_size, len(DATA))
            self.assertEqual(len(m.history), 2)
            self._clean_close(m)
            m2 = self._m()
            self.assertEqual(len(m2.snapshots()), 0)   # nothing left to resume
            m2.close()

    def test_gui_queue_pause_resume_real(self):
        with TestServer(RangeHandler) as srv:
            self.cfg.max_speed_bps = 512 * 1024  # slow enough to pause
            m = self._m(maxc=1)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(self.tmp)), autostart=True)
            self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
            m.pause_task(tid)
            self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.PAUSED))
            # Let any in-flight progress callback settle, then capture the value.
            for _ in range(10):
                paused_at = m.get(tid).completed
                time.sleep(0.05)
                if m.get(tid).completed == paused_at:
                    break
            time.sleep(0.3)
            self.assertEqual(m.get(tid).completed, paused_at)
            m.resume_task(tid)
            self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.COMPLETED))
            self.assertEqual((self.tmp / "file.bin").stat().st_size, len(DATA))
            self._clean_close(m)

    def test_restart_resume_real_subprocess_crash(self):
        """True crash (subprocess os._exit) mid-download → reopen → resume → complete."""
        with TestServer(RangeHandler) as srv:
            url = srv.url
            tmp = self.tmp
            root = Path(__file__).resolve().parent.parent
            child = r"""
import sys, time, os
sys.path.insert(0, r"%s")
from pathlib import Path
from config.settings import AppConfig
from core.session import SessionManager
from ui.common import DownloadRequest, TaskManager
from ui.legacy import LegacyDownloadRunner

cfg = AppConfig()
cfg.block_private_urls = False
cfg.num_threads = 4
cfg.verify_size = True
cfg.max_speed_bps = 512 * 1024
cfg.retry_delay = 0.01
cfg.retry_max_delay = 0.2
tmp = Path(r"%s")
runner = LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None)
m = TaskManager(runner, tmp, max_concurrent=1, config=cfg)
tid = m.add(DownloadRequest(url=r"%s", directory=str(tmp)), autostart=True)
deadline = time.time() + 30
while time.time() < deadline:
    snap = m.get(tid)
    if snap and snap.completed > 0:
        break
    time.sleep(0.05)
open(r"%s", "w").write(str(m.get(tid).completed))
os._exit(0)
""" % (root, tmp, url, tmp / "marker")
            proc = subprocess.run([sys.executable, "-c", child], cwd=str(root),
                                  capture_output=True, text=True, timeout=90)
            marker = tmp / "marker"
            self.assertTrue(marker.exists(), f"child never reached partial state: {proc.stderr}")
            partial_bytes = int(marker.read_text().strip() or "0")
            self.assertGreater(partial_bytes, 0)

            # Reopen in this process; the crashed child released all handles.
            self.cfg.max_speed_bps = 0
            m2 = self._m(maxc=1)
            snap = m2.snapshots()
            self.assertTrue(snap)
            self.assertEqual(snap[0].state, TaskStatus.QUEUED)
            self.assertGreaterEqual(snap[0].completed, 0)
            m2.start_all()
            self.assertTrue(wait_until(lambda: m2.get(snap[0].id).state == TaskStatus.COMPLETED))
            self.assertEqual((tmp / "file.bin").stat().st_size, len(DATA))
            m2.close()


if __name__ == "__main__":
    unittest.main()
