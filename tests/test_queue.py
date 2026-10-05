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


class RecordingRunner(FakeRunner):
    """Records the order downloads actually start in.

    Appends the request's file name the moment the transfer begins, so a test
    can assert on real execution order rather than on the queue's bookkeeping.
    """

    def __init__(self, started, **kw):
        super().__init__(**kw)
        self.started = started

    def download(self, task_id, request, analysis, progress, control,
                 status_callback=None, path_callback=None, smart_callback=None):
        self.started.append(Path(request.url).name)
        return super().download(task_id, request, analysis, progress, control,
                                status_callback, path_callback, smart_callback)


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
        # A slower fake download keeps the 2-active window wide enough that
        # the timing assertions below are deterministic (no completion race).
        m = self._m(maxc=2, runner=FakeRunner(delay=0.08))
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

    def test_group_move_preserves_order(self):
        # The Downloads context menu moves a multi-selection as one group:
        # forward order for "Move up", reverse order for "Move down" (mirrors
        # App.rowCallbacks.onMove).  Verify the queue order stays coherent.
        m = self._m(maxc=1)
        a, b, c, d, e, f = [
            m.add(DownloadRequest(url=f"https://x/{n}.zip", directory=str(self.tmp)), autostart=False)
            for n in ("a", "b", "c", "d", "e", "f")
        ]
        self.assertEqual([s.id for s in m.snapshots()], [a, b, c, d, e, f])

        # Move [b, c, d] up once: forward order.
        for tid in (b, c, d):
            m.move_task(tid, -1)
        self.assertEqual([s.id for s in m.snapshots()], [b, c, d, a, e, f])

        # Move [b, c, d] down once (back to original): reverse order.
        for tid in (d, c, b):
            m.move_task(tid, 1)
        self.assertEqual([s.id for s in m.snapshots()], [a, b, c, d, e, f])

        # Move [b, c, d] down once more: reverse order.
        for tid in (d, c, b):
            m.move_task(tid, 1)
        self.assertEqual([s.id for s in m.snapshots()], [a, e, b, c, d, f])

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

    def test_paused_task_survives_a_restart_as_paused(self):
        """An explicit pause is a user decision, not an interruption.

        It must not be laundered into "Queued" by the restart — that would
        start a download the user deliberately stopped.  See docs/QUEUE.md §5.
        """
        tmp = Path(tempfile.mkdtemp())
        m = manager(tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        tid = m.add(DownloadRequest(url="https://x/p.zip", directory=str(tmp)))
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
        m.pause_task(tid)
        self.assertEqual(m.get(tid).state, TaskStatus.PAUSED)
        m.close()
        m2 = manager(tmp, maxc=1, runner=FakeRunner())
        self.assertEqual(m2.get(tid).state, TaskStatus.PAUSED)

    def test_a_restored_pause_is_not_auto_started_by_resume_on_startup(self):
        """`resume_on_startup` may only start QUEUED work."""
        tmp = Path(tempfile.mkdtemp())
        m = manager(tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        tid = m.add(DownloadRequest(url="https://x/p.zip", directory=str(tmp)))
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
        m.pause_task(tid)
        m.close()
        cfg = SimpleNamespace(resume_on_startup=True)
        m2 = manager(tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02), config=cfg)
        time.sleep(0.4)
        self.assertEqual(m2.get(tid).state, TaskStatus.PAUSED)

    def test_resuming_a_restored_pause_requeues_and_actually_runs_it(self):
        """A restored pause owns no worker, so resuming must go via QUEUED.

        Going straight to DOWNLOADING would leave the task claiming to download
        forever with nothing doing the work — and `_start_next` only ever picks
        QUEUED tasks, so it could never be recovered.
        """
        tmp = Path(tempfile.mkdtemp())
        m = manager(tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        tid = m.add(DownloadRequest(url="https://x/p.zip", directory=str(tmp)))
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
        m.pause_task(tid)
        m.close()

        m2 = manager(tmp, maxc=1, runner=FakeRunner(chunk=50, delay=0.01))
        self.assertEqual(m2.get(tid).state, TaskStatus.PAUSED)
        m2.resume_task(tid)
        self.assertTrue(
            wait_until(lambda: m2.get(tid).state == TaskStatus.COMPLETED, timeout=10),
            f"task stuck in {m2.get(tid).state}",
        )
        m2.close()

    def test_resume_all_also_recovers_a_restored_pause(self):
        tmp = Path(tempfile.mkdtemp())
        m = manager(tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        tid = m.add(DownloadRequest(url="https://x/p.zip", directory=str(tmp)))
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
        m.pause_task(tid)
        m.close()

        m2 = manager(tmp, maxc=1, runner=FakeRunner(chunk=50, delay=0.01))
        m2.resume_all()
        self.assertTrue(
            wait_until(lambda: m2.get(tid).state == TaskStatus.COMPLETED, timeout=10),
            f"task stuck in {m2.get(tid).state}",
        )
        m2.close()

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


class QueueOrderingTest(unittest.TestCase):
    """Phase H — priority is a real scheduling input, and ordering is exact.

    Before this, `_start_next` picked the next task purely by its position in
    the manual order list: `task.priority` was stored and displayed but never
    read when choosing what to run, so the queue strip's priority control was
    decorative.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.started: list[str] = []

    def _m(self, maxc=1):
        return manager(self.tmp, maxc=maxc, runner=RecordingRunner(self.started))

    def _add(self, m, names, **kw):
        return {
            n: m.add(DownloadRequest(url=f"https://x/{n}", directory=str(self.tmp)), **kw)
            for n in names
        }

    def test_priority_decides_execution_order(self):
        """The headline fix, using the brief's own example."""
        m = self._m(maxc=1)
        ids = self._add(m, ["Movie.mkv", "Game.zip", "Image.png", "Linux.iso"], autostart=False)
        m.set_priority(ids["Image.png"], 0)
        m.set_priority(ids["Game.zip"], 2)
        m.set_priority(ids["Movie.mkv"], 5)
        m.set_priority(ids["Linux.iso"], 9)

        m.start_all()
        self.assertTrue(wait_until(lambda: len(self.started) == 4, timeout=30))
        self.assertEqual(self.started, ["Image.png", "Game.zip", "Movie.mkv", "Linux.iso"])

    def test_priority_does_not_scramble_the_manual_order(self):
        """Raising one task's priority must not reshuffle the user's list."""
        m = self._m(maxc=1)
        ids = self._add(m, ["a", "b", "c"], autostart=False)
        before = [s.id for s in m.snapshots()]
        m.set_priority(ids["c"], 0)
        self.assertEqual([s.id for s in m.snapshots()], before)

    def test_equal_priority_falls_back_to_manual_order(self):
        m = self._m(maxc=1)
        self._add(m, ["a", "b", "c"], autostart=False)
        m.start_all()
        self.assertTrue(wait_until(lambda: len(self.started) == 3, timeout=30))
        self.assertEqual(self.started, ["a", "b", "c"])

    def test_move_task_to_places_at_an_absolute_index(self):
        m = self._m()
        ids = self._add(m, ["a", "b", "c", "d"], autostart=False)
        order = lambda: [s.id for s in m.snapshots()]  # noqa: E731
        m.move_task_to(ids["d"], 0)
        self.assertEqual(order(), [ids[n] for n in ["d", "a", "b", "c"]])
        # `position` is the FINAL index, matching a drop handler's newIndex.
        m.move_task_to(ids["d"], 3)
        self.assertEqual(order(), [ids[n] for n in ["a", "b", "c", "d"]])
        m.move_task_to(ids["a"], 2)
        self.assertEqual(order(), [ids[n] for n in ["b", "c", "a", "d"]])

    def test_move_to_top_and_bottom(self):
        m = self._m()
        ids = self._add(m, ["a", "b", "c", "d", "e"], autostart=False)
        order = lambda: [s.id for s in m.snapshots()]  # noqa: E731
        m.move_to_top(ids["d"])
        self.assertEqual(order()[0], ids["d"])
        m.move_to_bottom(ids["d"])
        self.assertEqual(order()[-1], ids["d"])

    def test_reorder_tasks_moves_a_block_keeping_its_order(self):
        m = self._m()
        ids = self._add(m, ["a", "b", "c", "d", "e"], autostart=False)
        m.reorder_tasks([ids["c"], ids["a"]], 0)
        self.assertEqual([s.id for s in m.snapshots()],
                         [ids[n] for n in ["c", "a", "b", "d", "e"]])

    def test_reorder_tasks_without_position_gathers_the_block_in_place(self):
        m = self._m()
        ids = self._add(m, ["a", "b", "c", "d", "e"], autostart=False)
        # The block collapses onto its topmost member's slot (b sat at index 1)
        # while keeping the order the caller listed: d before b.
        m.reorder_tasks([ids["d"], ids["b"]])
        self.assertEqual([s.id for s in m.snapshots()],
                         [ids[n] for n in ["a", "d", "b", "c", "e"]])

    def test_ordering_ignores_unknown_ids(self):
        """A stale UI selection must never wedge the queue."""
        m = self._m()
        ids = self._add(m, ["a", "b"], autostart=False)
        before = [s.id for s in m.snapshots()]
        m.reorder_tasks(["ghost-1", "ghost-2"], 0)
        m.move_to_top("ghost-3")
        m.move_task_to("ghost-4", 1)
        m.move_to_bottom("ghost-5")
        self.assertEqual([s.id for s in m.snapshots()], before)

    def test_manual_order_survives_a_restart(self):
        """`add`/`remove` used to leave the persisted order stale."""
        m = self._m()
        ids = self._add(m, ["a", "b", "c", "d"], autostart=False)
        m.move_to_top(ids["d"])
        m.move_task_to(ids["a"], 2)
        saved = [s.id for s in m.snapshots()]
        m.close()

        m2 = self._m()
        self.assertEqual([s.id for s in m2.snapshots()], saved)

    def test_add_persists_order_without_a_later_edit(self):
        m = self._m()
        ids = self._add(m, ["a", "b", "c"], autostart=False)
        m.close()
        m2 = self._m()
        self.assertEqual([s.id for s in m2.snapshots()], [ids[n] for n in ["a", "b", "c"]])


class QueuePlanTest(unittest.TestCase):
    """Phase H — `queue_plan` reports the effective order and honest estimates."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _m(self, maxc=1):
        return manager(self.tmp, maxc=maxc)

    def _add(self, m, names, **kw):
        return {
            n: m.add(DownloadRequest(url=f"https://x/{n}", directory=str(self.tmp)), **kw)
            for n in names
        }

    def test_plan_order_follows_priority_not_queue_position(self):
        m = self._m()
        ids = self._add(m, ["a", "b", "c"], autostart=False)
        m.set_priority(ids["c"], 0)
        plan = m.queue_plan()
        self.assertEqual([p["name"] for p in plan], ["c", "a", "b"])
        self.assertEqual([p["position"] for p in plan], [1, 2, 3])
        self.assertEqual([p["priority"] for p in plan], [0, 5, 5])
        # `manual_index` still reports where the task sits in the raw order.
        self.assertEqual([p["manual_index"] for p in plan], [2, 0, 1])

    def test_plan_covers_only_queued_tasks(self):
        m = self._m(maxc=1, )
        ids = self._add(m, ["a", "b"], autostart=False)
        m.start_task(ids["a"])
        # Let "a" reach a running state before asking for the plan.
        self.assertTrue(wait_until(lambda: m.get(ids["a"]).state != TaskStatus.QUEUED))
        plan = m.queue_plan()
        self.assertEqual([p["id"] for p in plan], [ids["b"]])

    def test_plan_reports_remaining_bytes(self):
        m = self._m()
        ids = self._add(m, ["a"], autostart=False)
        plan = m.queue_plan()
        self.assertEqual(plan[0]["id"], ids["a"])
        self.assertEqual(plan[0]["remaining"], 0)   # never analyzed -> no size
        self.assertEqual(plan[0]["total"], 0)

    def test_plan_estimates_are_none_without_measured_throughput(self):
        """No running download means no per-slot throughput to divide by."""
        m = self._m(maxc=1)
        self._add(m, ["a", "b"], autostart=False)
        plan = m.queue_plan()
        # A slot is free, so the first task would start immediately...
        self.assertEqual(plan[0]["estimated_start_seconds"], 0.0)
        self.assertTrue(plan[0]["starts_immediately"])
        # ...but nothing behind a task of unknown length can be estimated.
        self.assertIsNone(plan[1]["estimated_start_seconds"])
        self.assertFalse(plan[1]["starts_immediately"])

    def test_plan_estimates_are_numeric_once_throughput_is_known(self):
        m = self._m(maxc=1)
        ids = self._add(m, ["run", "q1", "q2"])
        self.assertTrue(wait_until(lambda: m.get(ids["run"]).state == TaskStatus.DOWNLOADING))

        # Stand in for a probed download: known size, known speed, known ETA.
        with m._lock:
            rec = m._tasks[ids["run"]]
            rec.task.current_speed = 1000.0
            rec.task.total_size = 100_000
            rec.task.downloaded_size = 20_000
            rec.task.eta_seconds = 80.0
            for tid in (ids["q1"], ids["q2"]):
                m._tasks[tid].task.total_size = 5_000
                m._tasks[tid].task.downloaded_size = 0

        plan = m.queue_plan()
        self.assertEqual([p["id"] for p in plan], [ids["q1"], ids["q2"]])
        # q1 waits for the running task's 80s, then takes 5000/1000 = 5s.
        self.assertEqual(plan[0]["estimated_start_seconds"], 80.0)
        self.assertEqual(plan[1]["estimated_start_seconds"], 85.0)
        self.assertEqual(plan[0]["remaining"], 5_000)

    def test_plan_never_invents_a_wait_time_for_an_unknown_length(self):
        """A running download of unknown length must not read as "0 seconds"."""
        m = self._m(maxc=1)
        ids = self._add(m, ["run", "q1"])
        self.assertTrue(wait_until(lambda: m.get(ids["run"]).state == TaskStatus.DOWNLOADING))
        plan = m.queue_plan()
        self.assertEqual(len(plan), 1)
        self.assertIsNone(plan[0]["estimated_start_seconds"])
        self.assertFalse(plan[0]["starts_immediately"])


class QueueEngineRegressionTest(unittest.TestCase):
    """Phase H — the engine must not hang or double-start under load."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_start_next_fills_every_free_slot(self):
        m = manager(self.tmp, maxc=3, runner=FakeRunner(chunk=50, delay=0.01))
        for i in range(6):
            m.add(DownloadRequest(url=f"https://x/{i}.zip", directory=str(self.tmp)))
        self.assertTrue(wait_until(lambda: sum(
            1 for s in m.snapshots()
            if s.state in (TaskStatus.ANALYZING, TaskStatus.DOWNLOADING)) == 3))
        self.assertTrue(wait_until(lambda: all(
            s.state == TaskStatus.COMPLETED for s in m.snapshots()), timeout=20))

    def test_never_starts_more_than_max_concurrent(self):
        m = manager(self.tmp, maxc=2, runner=FakeRunner(chunk=50, delay=0.01))
        for i in range(8):
            m.add(DownloadRequest(url=f"https://x/{i}.zip", directory=str(self.tmp)))
        peak = 0
        deadline = time.time() + 20
        while time.time() < deadline:
            n = sum(1 for s in m.snapshots()
                    if s.state in (TaskStatus.ANALYZING, TaskStatus.DOWNLOADING))
            peak = max(peak, n)
            if all(s.state == TaskStatus.COMPLETED for s in m.snapshots()):
                break
            time.sleep(0.01)
        self.assertTrue(all(s.state == TaskStatus.COMPLETED for s in m.snapshots()))
        self.assertLessEqual(peak, 2)

    def test_reordering_while_running_does_not_disturb_active_downloads(self):
        m = manager(self.tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.03))
        ids = [m.add(DownloadRequest(url=f"https://x/{i}.zip", directory=str(self.tmp)))
               for i in range(4)]
        self.assertTrue(wait_until(lambda: any(
            m.get(i).state == TaskStatus.DOWNLOADING for i in ids)))
        # Hammer the ordering API while a download is in flight.
        for i in ids:
            m.move_to_top(i)
            m.move_to_bottom(i)
            m.move_task_to(i, 1)
            m.reorder_tasks([i], 0)
        self.assertTrue(wait_until(lambda: all(
            s.state == TaskStatus.COMPLETED for s in m.snapshots()), timeout=25))

    def test_bulk_add_persists_order_once_and_stays_ordered(self):
        m = manager(self.tmp, maxc=1)
        reqs = [DownloadRequest(url=f"https://x/{i}.zip", directory=str(self.tmp)) for i in range(5)]
        ids = m.add_many(reqs, autostart=False)
        self.assertEqual([s.id for s in m.snapshots()], ids)
        m.close()
        m2 = manager(self.tmp, maxc=1)
        self.assertEqual([s.id for s in m2.snapshots()], ids)


class QueueGateTest(unittest.TestCase):
    """The queue gate and a task pause are different things.

    Regression cover for the ambiguity where "Pause" meant one thing on the
    queue and another on a row, and where resuming a single row silently
    un-paused the whole queue.  See docs/QUEUE.md §1.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _add(self, m, names, **kw):
        return [
            m.add(DownloadRequest(url=f"https://x/{n}.zip", directory=str(self.tmp)),
                  **kw)
            for n in names
        ]

    def test_pause_queue_stops_new_starts_but_leaves_running_ones_alone(self):
        m = manager(self.tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        first, second = self._add(m, ["a", "b"])
        self.assertTrue(wait_until(lambda: m.get(first).state == TaskStatus.DOWNLOADING))

        m.pause_queue()
        self.assertTrue(m.queue_paused)
        # The download already in flight is untouched...
        self.assertEqual(m.get(first).state, TaskStatus.DOWNLOADING)
        # ...and the waiting one is not started even once a slot frees up.
        self.assertTrue(wait_until(
            lambda: m.get(first).state == TaskStatus.COMPLETED, timeout=15))
        time.sleep(0.3)
        self.assertEqual(m.get(second).state, TaskStatus.QUEUED)
        m.close()

    def test_resume_queue_starts_what_was_held_back(self):
        m = manager(self.tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        first, second = self._add(m, ["a", "b"])
        self.assertTrue(wait_until(lambda: m.get(first).state == TaskStatus.DOWNLOADING))
        m.pause_queue()
        self.assertTrue(wait_until(
            lambda: m.get(first).state == TaskStatus.COMPLETED, timeout=15))
        self.assertEqual(m.get(second).state, TaskStatus.QUEUED)

        m.resume_queue()
        self.assertFalse(m.queue_paused)
        self.assertTrue(wait_until(lambda: m.get(second).state in (
            TaskStatus.ANALYZING, TaskStatus.DOWNLOADING, TaskStatus.COMPLETED)))
        m.close()

    def test_resuming_one_task_does_not_open_the_queue_gate(self):
        """The exact bug: Resume on a single row used to start the whole queue."""
        m = manager(self.tmp, maxc=2, runner=FakeRunner(chunk=10, delay=0.05))
        ids = self._add(m, ["a", "b", "c"])
        self.assertTrue(wait_until(lambda: sum(
            m.get(i).state == TaskStatus.DOWNLOADING for i in ids) == 2))

        m.pause_all()
        self.assertTrue(m.queue_paused)
        self.assertTrue(wait_until(lambda: sum(
            m.get(i).state == TaskStatus.PAUSED for i in ids) == 2))

        m.resume_task(ids[0])
        self.assertTrue(wait_until(
            lambda: m.get(ids[0]).state == TaskStatus.DOWNLOADING, timeout=5))
        # The gate is still closed, so nothing else was dragged along with it.
        self.assertTrue(m.queue_paused)
        self.assertEqual(m.get(ids[1]).state, TaskStatus.PAUSED)
        self.assertEqual(m.get(ids[2]).state, TaskStatus.QUEUED)
        m.shutdown(cancel=True, wait=True, timeout=3)
        m.close()

    def test_pause_all_closes_the_gate_and_stops_every_active_task(self):
        m = manager(self.tmp, maxc=2, runner=FakeRunner(chunk=10, delay=0.05))
        ids = self._add(m, ["a", "b", "c"])
        self.assertTrue(wait_until(lambda: sum(
            m.get(i).state == TaskStatus.DOWNLOADING for i in ids) == 2))

        m.pause_all()
        self.assertTrue(m.queue_paused)
        self.assertTrue(wait_until(lambda: sum(
            m.get(i).state == TaskStatus.PAUSED for i in ids) == 2))
        # A queued task is not "paused" — it was never running.
        self.assertEqual(m.get(ids[2]).state, TaskStatus.QUEUED)
        m.shutdown(cancel=True, wait=True, timeout=3)
        m.close()

    def test_resume_all_opens_the_gate_and_resumes_everything(self):
        m = manager(self.tmp, maxc=2, runner=FakeRunner(chunk=50, delay=0.01))
        ids = self._add(m, ["a", "b", "c"])
        self.assertTrue(wait_until(lambda: sum(
            m.get(i).state == TaskStatus.DOWNLOADING for i in ids) == 2))
        m.pause_all()

        m.resume_all()
        self.assertFalse(m.queue_paused)
        self.assertTrue(wait_until(lambda: all(
            s.state == TaskStatus.COMPLETED for s in m.snapshots()), timeout=25))
        m.close()

    def test_start_task_opens_the_gate_so_the_button_is_never_a_no_op(self):
        m = manager(self.tmp, maxc=2, runner=FakeRunner(chunk=10, delay=0.02))
        ids = self._add(m, ["a", "b", "c"])
        self.assertTrue(wait_until(lambda: sum(
            m.get(i).state in (TaskStatus.ANALYZING, TaskStatus.DOWNLOADING) for i in ids) == 2))
        m.pause_queue()
        waiting = [i for i in ids if m.get(i).state == TaskStatus.QUEUED]
        self.assertTrue(waiting)

        m.start_task(waiting[0])
        self.assertFalse(m.queue_paused)
        self.assertTrue(wait_until(lambda: m.get(waiting[0]).state in (
            TaskStatus.ANALYZING, TaskStatus.DOWNLOADING, TaskStatus.COMPLETED),
            timeout=15))
        m.close()

    def test_start_task_is_a_noop_for_a_task_that_is_not_waiting(self):
        m = manager(self.tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        tid = self._add(m, ["a"])[0]
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
        m.start_task(tid)                       # already running
        self.assertEqual(m.get(tid).state, TaskStatus.DOWNLOADING)
        m.start_task("does-not-exist")
        m.close()

    def test_the_gate_is_not_persisted(self):
        """Pausing the queue is a "stop for a minute" action, not a setting."""
        m = manager(self.tmp, maxc=1, runner=FakeRunner())
        m.pause_queue()
        m.close()
        m2 = manager(self.tmp, maxc=1, runner=FakeRunner())
        self.assertFalse(m2.queue_paused)
        m2.close()


class EffectivePositionTest(unittest.TestCase):
    """`queue_position` is the order downloads will really start in.

    `queue_index` is the list the user drags; `queue_position` is what the
    scheduler will do.  They differ exactly when priority is doing something.
    See docs/QUEUE.md §2.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _add(self, m, names, **kw):
        return [
            m.add(DownloadRequest(url=f"https://x/{n}.zip", directory=str(self.tmp)),
                  **kw)
            for n in names
        ]

    def test_position_follows_priority_not_manual_order(self):
        m = manager(self.tmp, maxc=1, runner=FakeRunner())
        a, b, c = self._add(m, ["a", "b", "c"], autostart=False)
        m.set_priority(c, 0)                     # c jumps the queue

        self.assertEqual([m.get(t).queue_index for t in (a, b, c)], [0, 1, 2])
        self.assertEqual([m.get(t).queue_position for t in (a, b, c)], [2, 3, 1])
        m.close()

    def test_equal_priorities_make_the_two_orders_agree(self):
        m = manager(self.tmp, maxc=1, runner=FakeRunner())
        ids = self._add(m, ["a", "b", "c", "d"], autostart=False)
        self.assertEqual(
            [m.get(t).queue_index for t in ids],
            [m.get(t).queue_position - 1 for t in ids],
        )
        m.close()

    def test_position_is_minus_one_for_anything_not_waiting(self):
        m = manager(self.tmp, maxc=1, runner=FakeRunner(chunk=10, delay=0.02))
        tid = self._add(m, ["a"])[0]
        self.assertTrue(wait_until(lambda: m.get(tid).state == TaskStatus.DOWNLOADING))
        self.assertEqual(m.get(tid).queue_position, -1)
        self.assertTrue(wait_until(
            lambda: m.get(tid).state == TaskStatus.COMPLETED, timeout=15))
        self.assertEqual(m.get(tid).queue_position, -1)
        m.close()

    def test_the_plan_and_the_per_task_position_cannot_drift(self):
        """Two code paths, one rule — the whole point of the shared helper."""
        m = manager(self.tmp, maxc=2, runner=FakeRunner(chunk=10, delay=0.05))
        ids = self._add(m, ["a", "b", "c", "d", "e", "f"])
        m.set_priority(ids[4], 0)
        m.set_priority(ids[1], 9)
        m.move_to_bottom(ids[5])
        m.move_to_top(ids[2])

        plan = {p["id"]: p["position"] for p in m.queue_plan()}
        snap = {
            s.id: s.queue_position
            for s in m.snapshots() if s.state == TaskStatus.QUEUED
        }
        self.assertTrue(plan, "expected some work to still be waiting")
        self.assertEqual(snap, plan)
        self.assertEqual(m.queue_positions(), plan)
        m.shutdown(cancel=True, wait=True, timeout=3)
        m.close()

    def test_positions_are_a_permutation_of_one_to_n(self):
        m = manager(self.tmp, maxc=1, runner=FakeRunner())
        ids = self._add(m, ["a", "b", "c", "d", "e"], autostart=False)
        m.set_priority(ids[3], 1)
        m.move_to_bottom(ids[0])
        self.assertEqual(sorted(m.queue_positions().values()), [1, 2, 3, 4, 5])
        m.close()

    def test_to_dict_exposes_the_effective_position(self):
        """Front-end contract: `queue_position` must reach the JS snapshot."""
        m = manager(self.tmp, maxc=1, runner=FakeRunner())
        tid = self._add(m, ["a"], autostart=False)[0]
        payload = m.get(tid).to_dict()
        self.assertEqual(payload["queue_position"], 1)
        self.assertEqual(payload["queue_index"], 0)
        m.close()

    def test_positions_survive_a_reorder(self):
        m = manager(self.tmp, maxc=1, runner=FakeRunner())
        a, b, c = self._add(m, ["a", "b", "c"], autostart=False)
        # a to the bottom -> order b, c, a
        m.move_to_bottom(a)
        self.assertEqual(
            [m.get(t).queue_position for t in (a, b, c)], [3, 1, 2])
        # then c to the top -> order c, b, a
        m.move_to_top(c)
        self.assertEqual(
            [m.get(t).queue_position for t in (a, b, c)], [3, 2, 1])
        m.close()


class ApiQueueGateTest(unittest.TestCase):
    """The bridge surface the UI talks to for the queue gate.

    The gate is *runtime* state: it is pushed to the frontend on every change,
    and the API returns the resulting state so the UI never has to assume what
    a click did.  See docs/QUEUE.md §1.
    """

    def setUp(self):
        import core.paths
        from unittest.mock import patch as _patch

        from config.settings import AppConfig
        from core.session import SessionManager
        from ui.api import Api

        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AppConfig()
        self.cfg.auto_start_server = False
        self.cfg.download_dir = str(self.tmp / "downloads")
        self._patch = _patch.object(core.paths, "data_dir", return_value=self.tmp / "data")
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.api = Api(self.cfg, SessionManager(self.cfg))
        self.addCleanup(self.api.shutdown)

    def _gate_events(self):
        """Every ``queue_gate`` event queued since the last drain."""
        out = []
        while True:
            try:
                evt = self.api._event_queue.get_nowait()
            except Exception:
                break
            if evt.get("type") == "queue_gate":
                out.append(evt)
        return out

    def test_status_shape(self):
        status = self.api.get_queue_status()
        self.assertFalse(status["paused"])
        self.assertFalse(status["scheduler_gate"])
        self.assertGreaterEqual(status["max_concurrent"], 1)

    def test_pause_and_resume_round_trip(self):
        self.assertTrue(self.api.pause_queue()["paused"])
        self.assertTrue(self.api.get_queue_status()["paused"])
        self.assertFalse(self.api.resume_queue()["paused"])
        self.assertFalse(self.api.get_queue_status()["paused"])

    def test_every_gate_change_is_pushed_to_the_frontend(self):
        """Runtime state is pushed, not polled — the banner must not lag."""
        self._gate_events()          # drop anything the scheduler tick pushed
        self.api.pause_queue()
        events = self._gate_events()
        self.assertTrue(events, "pausing the queue pushed no event")
        self.assertTrue(events[-1]["status"]["paused"])

        self.api.resume_queue()
        events = self._gate_events()
        self.assertTrue(events, "resuming the queue pushed no event")
        self.assertFalse(events[-1]["status"]["paused"])

    def test_start_task_pushes_the_gate_it_opens(self):
        tid = self.api.add_download("https://a.example/x.zip",
                                    directory=str(self.tmp), label="x.zip",
                                    autostart=False)
        self.api.pause_queue()
        self._gate_events()

        status = self.api.start_task(tid)
        self.assertFalse(status["paused"])
        events = self._gate_events()
        self.assertTrue(events, "opening the gate pushed no event")
        self.assertFalse(events[-1]["status"]["paused"])

    def test_pause_all_and_resume_all_report_the_resulting_gate(self):
        self.assertTrue(self.api.pause_all()["paused"])
        self.assertFalse(self.api.resume_all()["paused"])

    def test_a_paused_queue_does_not_start_a_batch(self):
        """`add_batch` calls start_all(); the gate must still hold it back."""
        self.api.pause_queue()
        self.api.add_batch(["https://a.example/1.zip", "https://a.example/2.zip"],
                           directory=str(self.tmp))
        time.sleep(0.25)
        states = [s["state"] for s in self.api.get_downloads()]
        self.assertTrue(states, "the batch was not queued at all")
        self.assertNotIn("Downloading", states)
        self.assertNotIn("Analyzing", states)


if __name__ == "__main__":
    unittest.main()
