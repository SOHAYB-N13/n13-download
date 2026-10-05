"""Regression tests for the three confirmed release blockers.

Each test fails on the pre-fix code and passes after the fix:

1. Pause stops an in-flight transfer promptly (multi-segment, range-ignoring
   multi-part, and true single-thread / no-Range).
2. Graceful shutdown (prepare_for_exit) preserves resumable state, does not
   mark tasks FAILED/CANCELLED, and lets the process exit promptly.
3. A corrupt task database is quarantined and rebuilt instead of crashing the
   application.
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


def _written_bytes(out: Path) -> int:
    """Sum bytes in partial files (.partN / .tmp) — excludes final + .merging."""
    total = 0
    for p in out.glob("*"):
        name = p.name
        if ".part" in name or name.endswith(".tmp"):
            total += p.stat().st_size
    return total


def _wait_for(fn, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def _verify_file(out: Path, name: str = "file.bin") -> bool:
    f = out / name
    return (
        f.exists()
        and f.stat().st_size == len(DATA)
        and hashlib.sha256(f.read_bytes()).hexdigest() == hashlib.sha256(DATA).hexdigest()
    )


def _cfg(num_threads: int, speed: int) -> AppConfig:
    cfg = AppConfig()
    cfg.block_private_urls = False
    cfg.connection_mode = 'manual'
    cfg.num_threads = num_threads
    cfg.verify_size = True
    cfg.chunk_size = 256 * 1024  # small chunks -> responsive pause
    cfg.max_speed_bps = speed
    cfg.max_retries = 3
    cfg.retry_delay = 0.01
    cfg.retry_max_delay = 0.2
    return cfg


class PauseStopsTransferTest(unittest.TestCase):
    """Blocker #1 — Pause must stop writes promptly; Resume continues correctly."""

    def _assert_pause_stops(self, handler, num_threads):
        with TestServer(handler) as srv:
            cfg = _cfg(num_threads=num_threads, speed=512 * 1024)
            out = Path(tempfile.mkdtemp())
            ctl = TaskControl()
            done = {}
            progress = {"completed": 0}

            def cb(completed, _total):
                progress["completed"] = completed

            controller = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            th = threading.Thread(target=lambda: done.setdefault(
                "ok", controller.download_file(srv.url, out, control=ctl,
                                               progress_callback=cb)))
            th.start()
            # Wait until data is actually transferred, then pause.
            self.assertTrue(
                _wait_for(lambda: progress["completed"] > 100 * 1024),
                "transfer never produced data",
            )
            time.sleep(0.2)
            p0 = progress["completed"]
            ctl.pause()
            time.sleep(1.0)
            p1 = progress["completed"]
            delta = p1 - p0
            # Only an already-in-flight chunk may land; the transfer must NOT
            # continue writing megabytes while paused.
            self.assertLess(
                delta, 512 * 1024,
                f"transfer continued while paused: {delta} bytes",
            )
            ctl.resume()
            th.join(60)
            self.assertTrue(done.get("ok"), "download did not complete after resume")
            self.assertTrue(_verify_file(out), "resumed file is corrupt (size/checksum)")

    def test_multi_segment_range_pause(self):
        self._assert_pause_stops(RangeHandler, num_threads=4)

    def test_range_ignoring_multipart_pause(self):
        self._assert_pause_stops(NoRangeHandler, num_threads=4)

    def test_true_single_thread_norange_pause(self):
        self._assert_pause_stops(TrueNoRangeHandler, num_threads=1)


class ShutdownTest(unittest.TestCase):
    """Blocker #2 — graceful shutdown preserves resume state and exits promptly."""

    def test_shutdown_preserves_resumable_state(self):
        with TestServer(RangeHandler) as srv:
            cfg = _cfg(num_threads=2, speed=512 * 1024)
            tmp = Path(tempfile.mkdtemp())
            m = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                            tmp, max_concurrent=1, config=cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(tmp)), autostart=True)
            self.assertTrue(_wait_for(lambda: m.get(tid).state.value == "Downloading"))
            self.assertTrue(_wait_for(lambda: m.get(tid).completed > 0), "no bytes")
            m.prepare_for_exit()
            snap = m.get(tid)
            self.assertIn(snap.state.value, ("Downloading", "Paused"),
                          f"should be resumable, got {snap.state.value}")
            m.close()

            # Reopen: restored to the queue, not failed/cancelled.
            m2 = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                             tmp, max_concurrent=1, config=cfg)
            s2 = m2.get(tid)
            self.assertEqual(s2.state.value, "Queued")
            m2.close()

    def test_process_exits_promptly_after_shutdown(self):
        """A subprocess with an active download must exit quickly after the
        graceful shutdown path (pre-fix it hung on non-daemon pool threads)."""
        root = Path(__file__).resolve().parent.parent
        with TestServer(RangeHandler):
            code = r"""
import sys, time, threading, os, tempfile
sys.path.insert(0, r"%s")
from pathlib import Path
from config.settings import AppConfig
from core.session import SessionManager
from tests.helpers import RangeHandler, TestServer
from ui.common import DownloadRequest, TaskManager
from ui.legacy import LegacyDownloadRunner

def wait_until(cond, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if cond(): return True
        time.sleep(0.05)
    return False

with TestServer(RangeHandler) as srv:
    cfg = AppConfig()
    cfg.block_private_urls = False
    cfg.connection_mode = 'manual'
    cfg.num_threads = 2
    cfg.verify_size = True
    cfg.chunk_size = 256 * 1024
    cfg.max_speed_bps = 128 * 1024     # slow -> long throttle sleeps
    tmp = Path(tempfile.mkdtemp())
    m = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                    tmp, max_concurrent=1, config=cfg)
    tid = m.add(DownloadRequest(url=srv.url, directory=str(tmp)), autostart=True)
    assert wait_until(lambda: m.get(tid).state.value == "Downloading")
    time.sleep(0.2)
    m.prepare_for_exit()
    m.close()
    print("EXITED_CLEANLY")
""" % root
            proc = subprocess.run(
                [sys.executable, "-c", code], cwd=str(root),
                capture_output=True, text=True, timeout=25,
            )
            self.assertEqual(proc.returncode, 0, f"subprocess failed: {proc.stderr}")
            self.assertIn("EXITED_CLEANLY", proc.stdout)


class CorruptDatabaseTest(unittest.TestCase):
    """Blocker #3 — a corrupt task DB must not crash the app."""

    def test_taskmanager_recovers_from_corrupt_db(self):
        d = Path(tempfile.mkdtemp())
        db = d / "downloads.db"
        db.write_bytes(b"this is not a sqlite database at all........")
        cfg = _cfg(num_threads=2, speed=0)
        m = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                        d, max_concurrent=1, config=cfg)
        # Fresh db is usable.
        tid = m.add(DownloadRequest(url="https://x/a.zip", directory=str(d)), autostart=False)
        self.assertIsNotNone(m.get(tid))
        m.close()
        # Corrupt original preserved.
        self.assertTrue(any("corrupt" in p.name for p in d.iterdir()),
                        "corrupt database was not preserved")

    def test_fresh_db_schema_and_tasks(self):
        d = Path(tempfile.mkdtemp())
        cfg = _cfg(num_threads=2, speed=0)
        m = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                        d, max_concurrent=1, config=cfg)
        tid = m.add(DownloadRequest(url="https://x/b.zip", directory=str(d)), autostart=False)
        self.assertEqual(m.get(tid).category, "Archives")  # auto-categorised from .zip
        m.close()


if __name__ == "__main__":
    unittest.main()
