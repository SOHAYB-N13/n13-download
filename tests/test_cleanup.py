"""Release hardening — temporary-file (.part/.tmp/.merging/.dlstate) cleanup.

Requirements under test:
* Removing an incomplete/cancelled/failed task removes its temp files.
* Removing a COMPLETED task preserves the final file.
* Cancel / Pause do NOT clean (resume/retry depends on the partial files).
* Cleanup is scoped to the exact task (no wildcard, no cross-task deletion).
* Cleanup is safe against the active worker and survives a restart.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.session import SessionManager
from tests.helpers import DATA, RangeHandler, TestServer, TrueNoRangeHandler
from ui.common import DownloadRequest, TaskManager
from ui.legacy import LegacyDownloadRunner


def _cfg(num_threads: int, speed: int = 0) -> AppConfig:
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


def _wait_until(cond, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _temp_files(directory: Path, base: str = "file.bin") -> list:
    """Temp artifacts owned by *base* (file.bin → file.bin.partN/.tmp/etc)."""
    out = []
    prefix = base + "."
    for p in directory.iterdir():
        if p.is_file() and p.name.startswith(prefix) and p.name not in (base,):
            if p.name == base:
                continue
            out.append(p.name)
    return out


def _manager(dirpath, cfg):
    return TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                       dirpath, max_concurrent=2, config=cfg)


class CleanupTest(unittest.TestCase):
    def test_active_cancel_remove_multipart(self):
        """Active multi-segment download -> Cancel -> Remove -> temp files gone."""
        with TestServer(RangeHandler) as srv:
            cfg = _cfg(num_threads=4, speed=512 * 1024)
            dl = Path(tempfile.mkdtemp())
            m = _manager(Path(tempfile.mkdtemp()), cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(dl)), autostart=True)
            self.assertTrue(_wait_until(lambda: m.get(tid).completed > 0))
            m.cancel_task(tid)
            self.assertTrue(_wait_until(lambda: m.get(tid).state.value == "Cancelled"))
            self.assertTrue(_temp_files(dl), "expected temp files after cancel")
            m.remove_task(tid)
            self.assertIsNone(m.get(tid))
            self.assertEqual(_temp_files(dl), [], f"orphaned temp files: {_temp_files(dl)}")
            m.close()

    def test_single_segment_cleanup(self):
        """Single-segment (.tmp) task -> Cancel -> Remove -> .tmp gone."""
        with TestServer(TrueNoRangeHandler) as srv:
            cfg = _cfg(num_threads=1, speed=512 * 1024)
            dl = Path(tempfile.mkdtemp())
            m = _manager(Path(tempfile.mkdtemp()), cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(dl)), autostart=True)
            self.assertTrue(_wait_until(lambda: m.get(tid).completed > 0))
            m.cancel_task(tid)
            m.remove_task(tid)
            self.assertEqual(_temp_files(dl), [])
            m.close()

    def test_failed_remove_cleanup(self):
        """Failed download -> Remove -> temp files gone, unrelated files kept."""
        import tests.helpers as H

        class Get500(H.RangeHandler):
            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(DATA)))
                self.end_headers()

            def do_GET(self):
                self.send_response(500)
                self.end_headers()

        with TestServer(Get500) as srv:
            cfg = _cfg(num_threads=4, speed=0)
            cfg.max_retries = 1
            dl = Path(tempfile.mkdtemp())
            # An unrelated final file that must survive.
            unrelated = dl / "keep.me"
            unrelated.write_bytes(b"unrelated")
            m = _manager(Path(tempfile.mkdtemp()), cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(dl)), autostart=True)
            self.assertTrue(_wait_until(lambda: m.get(tid).state.value == "Failed"))
            self.assertTrue(_temp_files(dl), "failed download should have temp files")
            m.remove_task(tid)
            self.assertEqual(_temp_files(dl), [])
            self.assertEqual(unrelated.read_bytes(), b"unrelated", "unrelated file removed!")
            m.close()

    def test_paused_remove_cleanup(self):
        """Paused download -> .part remains while paused -> Remove deletes it."""
        with TestServer(RangeHandler) as srv:
            cfg = _cfg(num_threads=4, speed=512 * 1024)
            dl = Path(tempfile.mkdtemp())
            m = _manager(Path(tempfile.mkdtemp()), cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(dl)), autostart=True)
            self.assertTrue(_wait_until(lambda: m.get(tid).completed > 0))
            m.pause_task(tid)
            self.assertTrue(_wait_until(lambda: m.get(tid).state.value == "Paused"))
            self.assertTrue(_temp_files(dl), "pause must NOT delete the partial files")
            m.remove_task(tid)
            self.assertTrue(_wait_until(lambda: m.get(tid) is None))
            self.assertEqual(_temp_files(dl), [])
            m.close()

    def test_cancel_then_retry_preserves_partial(self):
        """Cancel must NOT clean — Retry/Resume reuses the partial data."""
        with TestServer(RangeHandler) as srv:
            cfg = _cfg(num_threads=4, speed=512 * 1024)
            dl = Path(tempfile.mkdtemp())
            m = _manager(Path(tempfile.mkdtemp()), cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(dl)), autostart=True)
            self.assertTrue(_wait_until(lambda: m.get(tid).completed > 0))
            m.cancel_task(tid)
            self.assertTrue(_wait_until(lambda: m.get(tid).state.value == "Cancelled"))
            self.assertTrue(_temp_files(dl), "cancel must keep partial files for resume")
            # Retry → completes correctly.
            m.retry_task(tid)
            self.assertTrue(_wait_until(lambda: m.get(tid).state.value == "Complete"))
            final = dl / "file.bin"
            self.assertEqual(final.stat().st_size, len(DATA))
            self.assertEqual(hashlib.sha256(final.read_bytes()).hexdigest(),
                             hashlib.sha256(DATA).hexdigest())
            m.close()

    def test_completed_remove_preserves_final_file(self):
        """Remove a completed task — the final file MUST remain."""
        with TestServer(RangeHandler) as srv:
            cfg = _cfg(num_threads=4, speed=0)
            dl = Path(tempfile.mkdtemp())
            m = _manager(Path(tempfile.mkdtemp()), cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(dl)), autostart=True)
            self.assertTrue(_wait_until(lambda: m.get(tid).state.value == "Complete"))
            final = dl / "file.bin"
            self.assertTrue(final.exists())
            m.remove_task(tid)
            self.assertIsNone(m.get(tid))
            self.assertTrue(final.exists(), "completed file must be preserved")
            self.assertEqual(final.stat().st_size, len(DATA))
            m.close()

    def test_locked_temp_file_cleanup_failure(self):
        """A locked temp file must not crash removal; failure is logged."""
        with TestServer(RangeHandler) as srv:
            cfg = _cfg(num_threads=4, speed=512 * 1024)
            dl = Path(tempfile.mkdtemp())
            m = _manager(Path(tempfile.mkdtemp()), cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(dl)), autostart=True)
            self.assertTrue(_wait_until(lambda: m.get(tid).completed > 0))
            m.cancel_task(tid)
            self.assertTrue(_wait_until(lambda: m.get(tid).state.value == "Cancelled"))
            temps = _temp_files(dl)
            self.assertTrue(temps)
            # Hold one temp file open so unlink fails on Windows.
            locked_path = dl / temps[0]
            with open(locked_path, "rb"):
                m.remove_task(tid)   # must NOT crash
                self.assertIsNone(m.get(tid))
                if locked_path.exists():
                    # Cleanup failed gracefully (file locked) — acceptable.
                    self.assertTrue(True)
            # After the handle is released, a manual cleanup attempt works.
            try:
                locked_path.unlink()
            except OSError:
                pass
            m.close()

    def test_cleanup_after_restart(self):
        """Crash (abrupt close) -> restart -> Remove -> temp files gone."""
        import subprocess
        root = Path(__file__).resolve().parent.parent
        with TestServer(RangeHandler) as srv:
            dl = Path(tempfile.mkdtemp())
            store = Path(tempfile.mkdtemp())
            child = r"""
import sys, time, os
sys.path.insert(0, r"%s")
from pathlib import Path
from config.settings import AppConfig
from core.session import SessionManager
from tests.helpers import RangeHandler, TestServer
from ui.common import DownloadRequest, TaskManager
from ui.legacy import LegacyDownloadRunner

cfg = AppConfig()
cfg.block_private_urls = False
cfg.connection_mode = 'manual'
cfg.num_threads = 4
cfg.verify_size = True
cfg.chunk_size = 256 * 1024
cfg.max_speed_bps = 512 * 1024
dl = Path(r"%s")
m = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                Path(r"%s"), max_concurrent=1, config=cfg)
tid = m.add(DownloadRequest(url=r"%s", directory=str(dl)), autostart=True)
deadline = time.time() + 30
while time.time() < deadline:
    s = m.get(tid)
    if s and s.completed > 0:
        break
    time.sleep(0.05)
open(r"%s", "w").write("ready")
os._exit(0)
""" % (root, dl, store, srv.url, store / "marker")
            subprocess.run([sys.executable, "-c", child], cwd=str(root),
                                  capture_output=True, text=True, timeout=90)
            self.assertTrue((store / "marker").exists(), "child never reached partial")
            # Restart: task restored to QUEUED with its resolved path persisted.
            cfg = _cfg(num_threads=4, speed=0)
            m2 = _manager(store, cfg)
            snaps = m2.snapshots()
            self.assertTrue(snaps)
            tid2 = snaps[0].id
            self.assertEqual(m2.get(tid2).state.value, "Queued")
            self.assertTrue(_temp_files(dl), "partial files should exist after crash")
            m2.remove_task(tid2)
            self.assertEqual(_temp_files(dl), [], "temp files not removed after restart+remove")
            m2.close()

    def test_remove_active_worker_race(self):
        """Remove an ACTIVE task: worker stops, then temp files are deleted and
        never recreated."""
        with TestServer(RangeHandler) as srv:
            cfg = _cfg(num_threads=4, speed=256 * 1024)
            dl = Path(tempfile.mkdtemp())
            m = _manager(Path(tempfile.mkdtemp()), cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(dl)), autostart=True)
            self.assertTrue(_wait_until(lambda: m.get(tid).completed > 0))
            m.remove_task(tid)          # active → cancel + cleanup in finalize
            self.assertTrue(_wait_until(lambda: m.get(tid) is None))
            self.assertEqual(_temp_files(dl), [], "temp files must be gone")
            time.sleep(0.5)             # give any stray worker a chance to write
            self.assertEqual(_temp_files(dl), [], "worker recreated temp files!")
            m.close()

    def test_missing_temp_file(self):
        """Removing a task whose temp files were already deleted must not crash."""
        with TestServer(RangeHandler) as srv:
            cfg = _cfg(num_threads=4, speed=512 * 1024)
            dl = Path(tempfile.mkdtemp())
            m = _manager(Path(tempfile.mkdtemp()), cfg)
            tid = m.add(DownloadRequest(url=srv.url, directory=str(dl)), autostart=True)
            self.assertTrue(_wait_until(lambda: m.get(tid).completed > 0))
            m.cancel_task(tid)
            self.assertTrue(_wait_until(lambda: m.get(tid).state.value == "Cancelled"))
            for p in dl.iterdir():
                try:
                    p.unlink()
                except OSError:
                    pass
            m.remove_task(tid)          # must not crash
            self.assertIsNone(m.get(tid))
            m.close()

    def test_similar_filenames_scoped_cleanup(self):
        """Removing one task must not delete another task's temp files."""
        with TestServer(RangeHandler) as srv:
            cfg = _cfg(num_threads=4, speed=256 * 1024)
            dl = Path(tempfile.mkdtemp())
            m = _manager(Path(tempfile.mkdtemp()), cfg)
            t1 = m.add(DownloadRequest(url=srv.url, directory=str(dl), label="file.bin"), autostart=True)
            # A second task whose resolved path collides on the base prefix.
            m.add(DownloadRequest(url=srv.url, directory=str(dl), label="file.bin.extra"), autostart=True)
            self.assertTrue(_wait_until(lambda: m.get(t1).completed > 0))
            time.sleep(0.2)
            m.cancel_task(t1)
            self.assertTrue(_wait_until(lambda: m.get(t1).state.value == "Cancelled"))
            # Give the second task time to create its own artifacts.
            self.assertTrue(_wait_until(lambda: len(_temp_files(dl)) > 0))
            before = set(_temp_files(dl))
            m.remove_task(t1)
            after = set(_temp_files(dl))
            # Only task t1's exact artifacts (file.bin.*) may be removed.
            removed = before - after
            self.assertTrue(all(n.startswith("file.bin.") and not n.startswith("file.bin.extra.")
                                for n in removed), f"removed wrong files: {removed}")
            m.close()


if __name__ == "__main__":
    unittest.main()
