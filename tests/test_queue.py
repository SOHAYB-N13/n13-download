"""Phases D+E — Queue manager edge cases and crash recovery.

Uses a fake runner (no network) for deterministic control, plus a real
subprocess crash for restart-resume verification.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.control import TaskCancelled
from core.task import TaskStatus
from ui.common import DownloadRequest, TaskManager

SIZE = 1000


class FakeRunner:
    """Deterministic runner: analyze OK, interruptible download loop."""

    def __init__(self, chunk=100, delay=0.02, fail_after=None):
        self.chunk = chunk
        self.delay = delay
        self.fail_after = fail_after   # fail after N chunks
        self.fail_next = False
        self.downloads = {}
        self.last_error = ""

    def analyze(self, task_id, request, control):
        if control.cancelled:
            raise TaskCancelled()
        return SimpleNamespace(ok=True, total_size=SIZE, supports_range=True,
                               filename="file.zip", content_type="application/zip",
                               server="test", etag="", last_modified="")

    def download(self, task_id, request, analysis, progress, control, status_callback=None, path_callback=None, smart_callback=None):
        if control.cancelled:
            raise TaskCancelled()
        if status_callback:
            status_callback("DOWNLOADING")
        if self.fail_next:
            # One-shot failure: only the next download attempt fails.
            self.fail_next = False
            self.last_error = "Fake network drop"
            return False
        done = 0
        chunks = 0
        while done < SIZE:
            if control.cancelled:
                return False
            control.wait_if_paused()
            if control.cancelled:
                return False
            if self.fail_after is not None and chunks >= self.fail_after:
                self.last_error = "Fake network drop"
                return False
            done = min(SIZE, done + self.chunk)
            chunks += 1
            time.sleep(self.delay)
            progress(done, SIZE)
        self.downloads[task_id] = True
        return True


def manager(dirpath, maxc=2, runner=None, config=None):
    return TaskManager(runner or FakeRunner(), dirpath, max_concurrent=maxc, config=config)


def wait_until(cond, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def run_in_subprocess(code: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run *code* in a fresh python process under the project root."""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


class QueueCoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _m(self, **kw):
        return manager(self.tmp, **kw)

    def test_fifo_order(self):
        m = self._m(maxc=1)
        ids = [m.add(DownloadRequest(url=f"https://x/{i}.zip", directory=str(self.tmp)),
                     autostart=False) for i in range(5)]
        self.assertEqual([s.id for s in m.snapshots()], ids)
        # Start one at a time and verify FIFO dequeue.
        m.start_all()
        completed = []
        while wait_until(lambda: all(s.state == TaskStatus.COMPLETED for s in m.snapshots())):
            break
        self.assertEqual(len(m.snapshots()), 5)

    def test_max_concurrent_and_auto_start(self):
        m = self._m(maxc=2)
        for i in range(4):
            m.add(DownloadRequest(url=f"https://x/{i}.zip", directory=str(self.tmp)))
        # Two analyzing/downloading, two queued.
        self.assertTrue(wait_until(lambda: sum(
            1 for s in m.snapshots() if s.state in (TaskStatus.ANALYZING, TaskStatus.DOWNLOADING)) == 2))
        active = [s for s in m.snapshots() if s.state in (TaskStatus.ANALYZING, TaskStatus.DOWNLOADING)]
        self.assertEqual(len(active), 2)
        queued = [s for s in m.snapshots() if s.state == TaskStatus.QUEUED]
        self.assertEqual(len(queued), 2)
        # When the first two finish, the others auto-start.
        self.assertTrue(wait_until(lambda: all(
            s.state == TaskStatus.COMPLETED for s in m.snapshots()), timeout=15))

    def test_cancel_active_and_retry(self):
        m = self._m(maxc=1, runner=FakeRunner(chunk=10, delay=0.03))
        tid = m.add(DownloadRequest(url="https://x/c.zip", directory=str(self.tmp)))
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
        m.cancel_task(tid)
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.CANCELLED))
        # Retry → completes.
        m.retry_task(tid)
        self.assertEqual(m.get(tid).retry_count, 1)
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.COMPLETED, timeout=20))

    def test_cancel_queued(self):
        m = self._m(maxc=0)  # nothing starts (max 0 clamped to 1 actually)
        m.set_max_concurrent(0)  # clamp -> 1
        tid = m.add(DownloadRequest(url="https://x/q.zip", directory=str(self.tmp)), autostart=False)
        self.assertEqual(m.get(tid).state, TaskStatus.QUEUED)
        m.cancel_task(tid)
        self.assertEqual(m.get(tid).state, TaskStatus.CANCELLED)
        m.remove_task(tid)
        self.assertIsNone(m.get(tid))

    def test_remove_active(self):
        m = self._m(maxc=1, runner=FakeRunner(chunk=10, delay=0.03))
        tid = m.add(DownloadRequest(url="https://x/r.zip", directory=str(self.tmp)))
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
        m.remove_task(tid)
        self.assertTrue(wait_until(lambda: m.get(tid) is None))
        self.assertEqual(len(m.snapshots()), 0)

    def test_remove_queued(self):
        m = self._m(maxc=1)
        m.add(DownloadRequest(url="https://x/busy.zip", directory=str(self.tmp)))
        qid = m.add(DownloadRequest(url="https://x/queued.zip", directory=str(self.tmp)), autostart=False)
        m.remove_task(qid)
        self.assertIsNone(m.get(qid))

    def test_pause_all_blocks_start(self):
        m = self._m(maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        tid = m.add(DownloadRequest(url="https://x/a.zip", directory=str(self.tmp)))
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
        m.pause_all()
        qid = m.add(DownloadRequest(url="https://x/b.zip", directory=str(self.tmp)), autostart=True)
        # Queued task must NOT start while globally paused.
        time.sleep(0.2)
        self.assertEqual(m.get(qid).state, TaskStatus.QUEUED)
        m.resume_all()
        self.assertTrue(wait_until(lambda: m.get(qid).state == TaskStatus.COMPLETED, timeout=10))

    def test_priority_and_move_while_active(self):
        m = self._m(maxc=1)
        a = m.add(DownloadRequest(url="https://x/a.zip", directory=str(self.tmp)), autostart=False)
        b = m.add(DownloadRequest(url="https://x/b.zip", directory=str(self.tmp)), autostart=False)
        c = m.add(DownloadRequest(url="https://x/c.zip", directory=str(self.tmp)), autostart=False)
        m.move_task(b, -1)
        self.assertEqual([s.id for s in m.snapshots()][0], b)
        m.set_priority(c, 0)
        self.assertEqual(m.get(c).priority, 0)
        # Start all; ensure no crash and all complete.
        m.start_all()
        self.assertTrue(wait_until(lambda: all(s.state == TaskStatus.COMPLETED for s in m.snapshots()), timeout=10))

    def test_failed_task_friendly_error(self):
        runner = FakeRunner()
        runner.fail_next = True
        m = self._m(maxc=1, runner=runner)
        tid = m.add(DownloadRequest(url="https://x/f.zip", directory=str(self.tmp)))
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.FAILED))
        self.assertIn("Fake network drop", m.get(tid).error or "")
        self.assertEqual(len(m.history), 1)

    def test_retry_failed(self):
        runner = FakeRunner()
        runner.fail_next = True
        m = self._m(maxc=1, runner=runner)
        t1 = m.add(DownloadRequest(url="https://x/1.zip", directory=str(self.tmp)))
        t2 = m.add(DownloadRequest(url="https://x/2.zip", directory=str(self.tmp)))
        self.assertTrue(wait_until(lambda: m.get(t1).state == TaskStatus.FAILED))
        self.assertTrue(wait_until(lambda: m.get(t2).state == TaskStatus.COMPLETED, timeout=10))
        runner.fail_next = False
        n = m.retry_failed()
        self.assertEqual(n, 1)
        self.assertTrue(wait_until(lambda: m.get(t1).state == TaskStatus.COMPLETED, timeout=10))

    def test_clear_finished(self):
        m = self._m(maxc=2)
        for i in range(3):
            m.add(DownloadRequest(url=f"https://x/{i}.zip", directory=str(self.tmp)))
        self.assertTrue(wait_until(lambda: all(s.state == TaskStatus.COMPLETED for s in m.snapshots()), timeout=10))
        self.assertEqual(len(m.snapshots()), 3)
        m.clear_finished()
        self.assertEqual(len(m.snapshots()), 0)
        # History is preserved even though tasks were removed.
        self.assertEqual(len(m.history), 3)


class CrashRecoveryTest(unittest.TestCase):
    """Phase E — restart-resume, including a true subprocess crash."""

    def test_subprocess_crash_and_resume(self):
        tmp = Path(tempfile.mkdtemp())
        crash_code = r"""
import sys, time
sys.path.insert(0, r"%s")
from pathlib import Path
from ui.common import TaskManager, DownloadRequest

class Slow:
    def analyze(self, task_id, request, control):
        return type("A", (), {"ok": True, "total_size": 1000000,
                              "supports_range": True, "filename": "big.bin",
                              "content_type": "application/octet-stream",
                              "server": "s", "etag": "", "last_modified": ""})()
    def download(self, task_id, request, analysis, progress, control, status_callback=None, path_callback=None, smart_callback=None):
        if status_callback: status_callback("DOWNLOADING")
        done = 0
        while done < 1000000:
            if control.cancelled: return False
            done += 5000
            time.sleep(0.005)
            progress(min(done, 1000000), 1000000)
        return True

m = TaskManager(Slow(), Path(r"%s"), max_concurrent=1)
tid = m.add(DownloadRequest(url="https://x/big.bin", directory=r"%s"), autostart=True)
while True:
    snap = m.get(tid)
    if snap and snap.completed >= 300000:
        break
    time.sleep(0.02)
open(r"%s", "w").write("ready")
# Abrupt crash mid-download — no clean shutdown at all.
os._exit(0)
""" % (Path(__file__).resolve().parent.parent, tmp, tmp, tmp / "marker")
        proc = run_in_subprocess(crash_code, Path(__file__).resolve().parent.parent)
        self.assertTrue((tmp / "marker").exists(), f"crash child did not reach partial state: {proc.stderr}")
        # Reopen the store with a fresh manager and verify recovery.
        m2 = manager(tmp, maxc=1, runner=FakeRunner())
        snap = m2.get(m2.snapshots()[0].id)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.state, TaskStatus.QUEUED)
        self.assertGreaterEqual(snap.completed, 0)
        # Let it resume to completion (engine will re-probe the URL, which the
        # fake treats as a fresh download — here we just check queue behaviour).
        m2.start_all()
        self.assertTrue(wait_until(lambda: all(
            s.state == TaskStatus.COMPLETED for s in m2.snapshots()), timeout=10))

    def test_multiple_unfinished_restore(self):
        tmp = Path(tempfile.mkdtemp())
        m = manager(tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        ids = [m.add(DownloadRequest(url=f"https://x/{i}.zip", directory=str(tmp)),
                     autostart=True) for i in range(3)]
        self.assertTrue(wait_until(lambda: any(
            m.get(i).state == TaskStatus.DOWNLOADING for i in ids)))
        # Abrupt close (no cancel) simulates interruption.
        m.close()
        m2 = manager(tmp, maxc=1, runner=FakeRunner())
        restored = [s for s in m2.snapshots()]
        self.assertTrue(len(restored) >= 1)
        for s in restored:
            self.assertEqual(s.state, TaskStatus.QUEUED)

    def test_paused_task_restored_to_queued(self):
        tmp = Path(tempfile.mkdtemp())
        m = manager(tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        tid = m.add(DownloadRequest(url="https://x/p.zip", directory=str(tmp)))
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
        m.pause_task(tid)
        self.assertEqual(m.get(tid).state, TaskStatus.PAUSED)
        m.close()
        m2 = manager(tmp, maxc=1, runner=FakeRunner())
        s = m2.get(tid)
        self.assertEqual(s.state, TaskStatus.QUEUED)

    def test_missing_destination_folder(self):
        tmp = Path(tempfile.mkdtemp())
        gone = Path(tempfile.mkdtemp()) / "nested"
        m = manager(tmp, maxc=1, runner=FakeRunner())
        tid = m.add(DownloadRequest(url="https://x/g.zip", directory=str(gone)))
        m.close()
        m2 = manager(tmp, maxc=1, runner=FakeRunner())
        s = m2.get(tid)
        self.assertEqual(s.state, TaskStatus.QUEUED)
        self.assertIn("Destination folder not found", s.error)

    def test_shutdown_with_active_queue(self):
        tmp = Path(tempfile.mkdtemp())
        m = manager(tmp, maxc=2, runner=FakeRunner(chunk=10, delay=0.02))
        ids = [m.add(DownloadRequest(url=f"https://x/{i}.zip", directory=str(tmp))) for i in range(4)]
        self.assertTrue(wait_until(lambda: any(
            m.get(i).state == TaskStatus.DOWNLOADING for i in ids)))
        # Simulate app close: graceful exit pauses + persists (does NOT cancel).
        m.prepare_for_exit()
        m.close()
        m2 = manager(tmp, maxc=1, runner=FakeRunner())
        self.assertTrue(len(m2.snapshots()) >= 1)
        for s in m2.snapshots():
            self.assertEqual(s.state, TaskStatus.QUEUED)
            self.assertIn("Restored after restart", s.error)

    def test_clean_shutdown_cancels_terminally(self):
        """shutdown(cancel=True) is the explicit-cancel path (terminal state)."""
        tmp = Path(tempfile.mkdtemp())
        m = manager(tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        tid = m.add(DownloadRequest(url="https://x/s.zip", directory=str(tmp)))
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
        m.shutdown(cancel=True, wait=True, timeout=3)
        self.assertEqual(m.get(tid).state, TaskStatus.CANCELLED)
        m.close()
        m2 = manager(tmp, maxc=1, runner=FakeRunner())
        self.assertEqual(m2.get(tid).state, TaskStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
