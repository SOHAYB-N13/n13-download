"""Regression + Phase F — engine-level tests against a real local HTTP server.

Covers: normal multi-threaded download, Range, non-Range fallback, resume,
pause/cancel, checksum verify (pass + fail), 404, SSRF blocking, speed
throttling, transient-error retry, and per-task control isolation.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.control import TaskControl
from core.download import DownloadController
from core.security import validate_download_url
from core.session import SessionManager
from tests.helpers import DATA, NotFoundHandler, NoRangeHandler, RangeHandler, TestServer, test_config


class EngineBasicsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _controller(self, **cfg_kw):
        cfg = test_config(**cfg_kw)
        return cfg, DownloadController(cfg, SessionManager(cfg), show_progress=False)

    def test_normal_multithreaded_download(self):
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller(num_threads=4)
            self.assertTrue(c.download_file(srv.url, self.dir))
            saved = self.dir / "file.bin"
            self.assertEqual(saved.stat().st_size, len(DATA))
            self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(),
                             hashlib.sha256(DATA).hexdigest())

    def test_parallel_parts_used(self):
        # With range support + 4 threads, part files must appear and merge.
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller(num_threads=4)
            self.assertTrue(c.download_file(srv.url, self.dir))
            self.assertTrue((self.dir / "file.bin").exists())
            # .part files cleaned up after merge
            self.assertEqual(list(self.dir.glob("*.part*")), [])

    def test_non_range_fallback(self):
        with TestServer(NoRangeHandler) as srv:
            cfg, c = self._controller(num_threads=4)
            self.assertTrue(c.download_file(srv.url, self.dir))
            saved = self.dir / "file.bin"
            self.assertEqual(saved.stat().st_size, len(DATA))

    def test_non_range_first_download_fresh_process(self):
        """Regression: a single-thread (non-Range) download as the *first*
        download in a fresh process must not hang on the pause blocker."""
        import subprocess
        root = Path(__file__).resolve().parent.parent
        with TestServer(NoRangeHandler) as srv:
            code = r"""
import sys, tempfile
sys.path.insert(0, r"%s")
from pathlib import Path
from config.settings import AppConfig
from core.session import SessionManager
from core.download import DownloadController
from tests.helpers import DATA, NoRangeHandler, TestServer
with TestServer(NoRangeHandler) as srv:
    cfg = AppConfig(); cfg.block_private_urls = False; cfg.verify_size = True
    c = DownloadController(cfg, SessionManager(cfg), show_progress=False)
    out = Path(tempfile.mkdtemp())
    assert c.download_file(srv.url, out), c.last_error
    assert (out / "file.bin").stat().st_size == len(DATA)
    print("OK")
""" % root
            proc = subprocess.run([sys.executable, "-c", code], cwd=str(root),
                                  capture_output=True, text=True, timeout=90)
            self.assertIn("OK", proc.stdout, f"child failed: {proc.stderr}")

    def test_resume_after_partial(self):
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller(num_threads=2, max_speed_bps=512 * 1024)
            control = TaskControl()
            result = {}
            th = threading.Thread(target=lambda: result.setdefault(
                "ok", c.download_file(srv.url, self.dir, control=control,
                                      progress_callback=lambda d, t: result.update(done=d))))
            th.start()
            deadline = time.time() + 20
            while time.time() < deadline:
                if result.get("done", 0) > 0:
                    control.cancel()
                    break
                time.sleep(0.02)
            th.join(20)
            # State saved for resume.
            self.assertTrue(list(self.dir.glob("*.dlstate")))
            # Resume to completion.
            cfg2, c2 = self._controller(num_threads=2)
            self.assertTrue(c2.download_file(srv.url, self.dir))
            saved = self.dir / "file.bin"
            self.assertEqual(saved.stat().st_size, len(DATA))
            self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(),
                             hashlib.sha256(DATA).hexdigest())

    def test_checksum_verify_ok(self):
        expected = hashlib.sha256(DATA).hexdigest()
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller()
            self.assertTrue(c.download_file(srv.url, self.dir,
                                            verify_checksum=True, expected_hash=expected))
            self.assertTrue((self.dir / "file.bin").exists())

    def test_checksum_verify_mismatch(self):
        bogus = hashlib.sha256(b"not the data").hexdigest()
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller()
            self.assertFalse(c.download_file(srv.url, self.dir,
                                             verify_checksum=True, expected_hash=bogus))
            # Corrupt result is removed, not left behind.
            self.assertFalse((self.dir / "file.bin").exists())

    def test_404_friendly_error(self):
        with TestServer(NotFoundHandler) as srv:
            cfg, c = self._controller()
            self.assertFalse(c.download_file(srv.url, self.dir))
            self.assertIn("404", c.last_error)

    def test_ssrf_blocks_loopback(self):
        with TestServer(RangeHandler) as srv:
            ok, err = validate_download_url(srv.url, block_private=True)
            self.assertFalse(ok)
            # And the engine refuses too when protection is on.
            cfg = AppConfig()          # block_private_urls defaults True
            c = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            self.assertFalse(c.download_file(srv.url, self.dir))

    def test_speed_throttling(self):
        # 1 MB/s cap over ~5 MB (with a 1 MB soft burst) must take a couple of
        # seconds — comfortably above the ~0.1 s an unlimited download takes.
        with TestServer(RangeHandler) as srv:
            start = time.monotonic()
            cfg, c = self._controller(num_threads=1, max_speed_bps=1024 * 1024)
            self.assertTrue(c.download_file(srv.url, self.dir))
            elapsed = time.monotonic() - start
            self.assertGreater(elapsed, 2.0, f"too fast ({elapsed:.2f}s) — limiter not applied")

    def test_transient_server_error_retried(self):
        RangeHandler.fail_requests = 1   # first range GET returns 500
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller(max_retries=4, retry_delay=0.01)
            self.assertTrue(c.download_file(srv.url, self.dir))
            self.assertEqual((self.dir / "file.bin").stat().st_size, len(DATA))

    def test_per_task_cancel_isolation(self):
        """Cancelling one task's control must not cancel a concurrent one."""
        with TestServer(RangeHandler) as srv:
            cfg = test_config(num_threads=1, max_speed_bps=512 * 1024)
            c1 = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            c2 = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            out1, out2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
            ctl1, ctl2 = TaskControl(), TaskControl()
            r1, r2 = {}, {}
            t1 = threading.Thread(target=lambda: r1.setdefault(
                "ok", c1.download_file(srv.url, out1, control=ctl1,
                                       progress_callback=lambda d, t: r1.update(done=d))))
            t2 = threading.Thread(target=lambda: r2.setdefault(
                "ok", c2.download_file(srv.url, out2, control=ctl2,
                                       progress_callback=lambda d, t: r2.update(done=d))))
            t1.start(); t2.start()
            # Let both get going, then cancel only task 1.
            deadline = time.time() + 20
            while time.time() < deadline:
                if r1.get("done", 0) > 0 and r2.get("done", 0) > 0:
                    ctl1.cancel()
                    break
                time.sleep(0.02)
            t1.join(20); t2.join(20)
            self.assertTrue(not r1.get("ok", False))        # task1 cancelled
            self.assertTrue(r2.get("ok", False))            # task2 unaffected
            self.assertEqual((out2 / "file.bin").stat().st_size, len(DATA))


if __name__ == "__main__":
    unittest.main()
