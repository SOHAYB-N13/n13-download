"""Release hardening — pause/resume stress and shutdown stress.

Stress tests must show: no corruption, no duplicate/missing ranges, no
deadlock, no CPU busy-loop (event-based pause), prompt shutdown, and no task
incorrectly marked FAILED after a graceful exit.
"""

from __future__ import annotations

import hashlib
import subprocess
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
from core.session import SessionManager
from tests.helpers import DATA, NoRangeHandler, RangeHandler, TestServer, TrueNoRangeHandler
from ui.common import DownloadRequest, TaskManager
from ui.legacy import LegacyDownloadRunner


def _cfg(num_threads: int, speed: int) -> AppConfig:
    cfg = AppConfig()
    cfg.block_private_urls = False
    cfg.connection_mode = 'manual'
    cfg.num_threads = num_threads
    cfg.verify_size = True
    cfg.chunk_size = 256 * 1024
    cfg.max_speed_bps = speed
    cfg.max_retries = 3
    cfg.retry_delay = 0.01
    cfg.retry_max_delay = 0.2
    return cfg


def _wait_for(fn, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def _verify(out: Path) -> bool:
    f = out / "file.bin"
    return f.exists() and f.stat().st_size == len(DATA) and \
        hashlib.sha256(f.read_bytes()).hexdigest() == hashlib.sha256(DATA).hexdigest()


class PauseResumeStressTest(unittest.TestCase):
    def _stress(self, handler, num_threads):
        with TestServer(handler) as srv:
            cfg = _cfg(num_threads=num_threads, speed=512 * 1024)
            out = Path(tempfile.mkdtemp())
            ctl = TaskControl()
            done = {}
            progress = {"c": 0}

            def cb(completed, _total):
                progress["c"] = completed

            ctrl = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            th = threading.Thread(target=lambda: done.setdefault(
                "ok", ctrl.download_file(srv.url, out, control=ctl, progress_callback=cb)))
            th.start()
            self.assertTrue(_wait_for(lambda: progress["c"] > 50 * 1024),
                            f"{handler.__name__}: no progress")
            # Pause -> Resume repeatedly while the transfer is active.
            for _ in range(5):
                ctl.pause()
                time.sleep(0.15)
                self.assertTrue(ctl.paused)
                ctl.resume()
                time.sleep(0.15)
                self.assertFalse(ctl.paused)
                self.assertFalse(done.get("ok", False), "completed while paused-looping")
            ctl.resume()
            th.join(60)
            self.assertTrue(done.get("ok"), f"{handler.__name__}: not completed")
            self.assertTrue(_verify(out), f"{handler.__name__}: corrupt after stress")

    def test_stress_multipart_range(self):
        self._stress(RangeHandler, 4)

    def test_stress_range_ignoring(self):
        self._stress(NoRangeHandler, 4)

    def test_stress_single_thread_no_range(self):
        self._stress(TrueNoRangeHandler, 1)


class ShutdownStressTest(unittest.TestCase):
    def test_repeated_shutdown_and_restore(self):
        for _ in range(3):
            with TestServer(RangeHandler) as srv:
                cfg = _cfg(num_threads=2, speed=512 * 1024)
                tmp = Path(tempfile.mkdtemp())
                m = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                                tmp, max_concurrent=1, config=cfg)
                tid = m.add(DownloadRequest(url=srv.url, directory=str(tmp)), autostart=True)
                self.assertTrue(_wait_for(lambda: m.get(tid).completed > 0))
                m.prepare_for_exit()
                self.assertIn(m.get(tid).state.value, ("Downloading", "Paused"),
                              f"cycle: state became {m.get(tid).state.value}")
                m.close()
                m2 = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                                 tmp, max_concurrent=1, config=cfg)
                self.assertEqual(m2.get(tid).state.value, "Queued")
                # Resume to completion so the next cycle starts clean.
                m2.start_all()
                self.assertTrue(_wait_for(lambda: m2.get(tid).state.value == "Complete"))
                m2.close()

    def test_shutdown_while_paused(self):
        with TestServer(RangeHandler) as srv:
            cfg = _cfg(num_threads=2, speed=512 * 1024)
            tmp = Path(tempfile.mkdtemp())
            m = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                            tmp, max_concurrent=1, config=cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(tmp)), autostart=True)
            self.assertTrue(_wait_for(lambda: m.get(tid).completed > 0))
            m.pause_task(tid)
            self.assertEqual(m.get(tid).state.value, "Paused")
            m.prepare_for_exit()
            # Paused + shutdown: the worker is unblocked by cancel but the
            # record must NOT become CANCELLED/FAILED.
            self.assertIn(m.get(tid).state.value, ("Downloading", "Paused"))
            m.close()
            m2 = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                             tmp, max_concurrent=1, config=cfg)
            self.assertEqual(m2.get(tid).state.value, "Queued")
            m2.close()

    def test_shutdown_during_retry(self):
        """Engine stuck retrying a 500 on GET (probe/HEAD succeeds); shutdown
        must stop it promptly and restore the task, never FAILED."""
        from tests.helpers import RangeHandler as RH

        class Get500(RH):
            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(DATA)))
                self.end_headers()

            def do_GET(self):
                self.send_response(500)
                self.end_headers()

        with TestServer(Get500) as srv:
            cfg = _cfg(num_threads=2, speed=0)
            cfg.max_retries = 20
            tmp = Path(tempfile.mkdtemp())
            m = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                            tmp, max_concurrent=1, config=cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(tmp)), autostart=True)
            # Let it get stuck retrying the 500s (part backoff), then shut down.
            self.assertTrue(_wait_for(lambda: m.get(tid).state.value == "Downloading"))
            time.sleep(0.3)
            m.prepare_for_exit()
            snap = m.get(tid)
            self.assertIn(snap.state.value, ("Downloading", "Paused", "Queued"),
                          f"became {snap.state.value}")
            m.close()
            m2 = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                             tmp, max_concurrent=1, config=cfg)
            self.assertEqual(m2.get(tid).state.value, "Queued")
            m2.close()


if __name__ == "__main__":
    unittest.main()
