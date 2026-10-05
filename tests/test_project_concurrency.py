"""Project layer — project-aware scheduling and concurrency.

The queue's two ceilings must hold at the same time: a project may never exceed
its own limit, and no combination of projects may exceed the application-wide
one.  These tests drive the real ``TaskManager`` with a runner that parks inside
``download()``, so "how many are running" is a fact the test controls rather than
a timing guess.

Also covered here: a paused project must not stop its neighbours, a closed
schedule must not stall the queue behind it, and repeated gate pushes must never
produce a second worker for the same task.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.control import TaskCancelled
from core.db import DEFAULT_PROJECT_ID
from core.task import ACTIVE_STATES, TaskStatus
from projects.admission import ProjectGate
from ui.common import DownloadRequest, TaskManager

SIZE = 1000


class GatedRunner:
    """A runner whose downloads park until the test releases them.

    ``analyze`` returns immediately; ``download`` signals that it has begun and
    then blocks.  That makes the number of live workers observable and stable,
    which is the only way to assert on a concurrency limit without racing.
    """

    def __init__(self):
        self.release = threading.Event()
        self.begun = threading.Event()
        self._lock = threading.Lock()
        self.started: list[str] = []
        self.last_error = ""

    def analyze(self, task_id, request, control):
        if control.cancelled:
            raise TaskCancelled()
        return SimpleNamespace(
            ok=True, total_size=SIZE, supports_range=True, filename="file.zip",
            content_type="application/zip", server="test", etag="", last_modified="",
        )

    def download(self, task_id, request, analysis, progress, control,
                 status_callback=None, path_callback=None, smart_callback=None):
        if control.cancelled:
            raise TaskCancelled()
        if status_callback:
            status_callback("DOWNLOADING")
        with self._lock:
            self.started.append(request.url)
        self.begun.set()
        # Park until released, but stay responsive to cancellation.
        while not self.release.wait(0.02):
            if control.cancelled:
                return False
        progress(SIZE, SIZE)
        return True

    def release_all(self):
        self.release.set()

    def count_started(self) -> int:
        with self._lock:
            return len(self.started)


def wait_until(cond, timeout=8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.runner = GatedRunner()
        self.manager = None

    def tearDown(self):
        if self.manager is not None:
            try:
                self.runner.release_all()
                self.manager.close()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _manager(self, maxc=2, gates=None) -> TaskManager:
        self.manager = TaskManager(
            self.runner, self.tmp, max_concurrent=maxc, project_gates=gates or {}
        )
        return self.manager

    def _add(self, manager, project_id: str, name: str, autostart=False) -> str:
        return manager.add(
            DownloadRequest(
                url=f"https://x/{name}",
                directory=str(self.tmp),
                label=name,
                project_id=project_id,
            ),
            autostart=autostart,
        )

    def _wait_active(self, manager, expected: int, timeout=8.0) -> bool:
        return wait_until(lambda: manager._active == expected, timeout)


# --------------------------------------------------------------------------- #
# The application-wide ceiling
# --------------------------------------------------------------------------- #


class GlobalLimitTest(_Base):
    def test_the_global_limit_is_never_exceeded_across_projects(self):
        m = self._manager(maxc=2)
        self._add(m, "A", "a1")
        self._add(m, "A", "a2")
        self._add(m, "B", "b1")
        self._add(m, "B", "b2")

        m.start_all()

        self.assertTrue(self._wait_active(m, 2))
        time.sleep(0.15)
        self.assertEqual(m._active, 2, "no more than max_concurrent workers may run")
        self.assertEqual(sum(m.project_active_counts().values()), m._active)

    def test_a_slot_freed_by_one_project_is_taken_by_another(self):
        m = self._manager(maxc=1)
        self._add(m, "A", "a1")
        self._add(m, "B", "b1")
        m.start_all()
        self.assertTrue(self._wait_active(m, 1))

        # Let the transfers run to completion.  Both must finish: with one slot
        # and a shared release, B can only run after A has handed the slot back,
        # and it completes so fast that polling for a live B worker would race.
        self.runner.release_all()

        self.assertTrue(wait_until(
            lambda: all(s.state == TaskStatus.COMPLETED for s in m.snapshots())
        ))
        self.assertIn("https://x/b1", self.runner.started, "the freed slot was never handed on")
        self.assertEqual(m._active, 0)

    def test_the_per_project_counts_always_sum_to_the_global_count(self):
        m = self._manager(maxc=3)
        for pid, name in (("A", "a1"), ("A", "a2"), ("B", "b1")):
            self._add(m, pid, name)
        m.start_all()
        self.assertTrue(self._wait_active(m, 3))
        self.assertEqual(sum(m.project_active_counts().values()), m._active)
        self.assertEqual(m.project_active_count("A"), 2)
        self.assertEqual(m.project_active_count("B"), 1)


# --------------------------------------------------------------------------- #
# The per-project ceiling
# --------------------------------------------------------------------------- #


class ProjectLimitTest(_Base):
    def test_a_project_never_exceeds_its_own_limit(self):
        m = self._manager(maxc=5, gates={"A": ProjectGate(limit=1)})
        self._add(m, "A", "a1")
        self._add(m, "A", "a2")
        self._add(m, "A", "a3")

        m.start_all()

        self.assertTrue(self._wait_active(m, 1))
        time.sleep(0.15)
        self.assertEqual(m.project_active_count("A"), 1, "project ceiling ignored")
        self.assertEqual(self.runner.count_started(), 1)

    def test_a_project_ceiling_does_not_hold_back_other_projects(self):
        m = self._manager(maxc=3, gates={"A": ProjectGate(limit=1)})
        self._add(m, "A", "a1")
        self._add(m, "A", "a2")
        self._add(m, "B", "b1")

        m.start_all()

        self.assertTrue(self._wait_active(m, 2))
        self.assertEqual(m.project_active_count("A"), 1)
        self.assertEqual(m.project_active_count("B"), 1)

    def test_a_project_limit_above_the_global_one_still_respects_the_global_one(self):
        m = self._manager(maxc=2, gates={"A": ProjectGate(limit=50)})
        for i in range(4):
            self._add(m, "A", f"a{i}")

        m.start_all()

        self.assertTrue(self._wait_active(m, 2))
        time.sleep(0.15)
        self.assertEqual(m._active, 2, "the global ceiling must still apply")

    def test_a_limit_of_zero_inherits_the_global_ceiling(self):
        m = self._manager(maxc=2, gates={"A": ProjectGate(limit=0)})
        for i in range(4):
            self._add(m, "A", f"a{i}")
        m.start_all()
        self.assertTrue(self._wait_active(m, 2))

    def test_raising_a_limit_starts_the_waiting_work(self):
        m = self._manager(maxc=5, gates={"A": ProjectGate(limit=1)})
        self._add(m, "A", "a1")
        self._add(m, "A", "a2")
        m.start_all()
        self.assertTrue(self._wait_active(m, 1))

        m.set_project_gates({"A": ProjectGate(limit=2)})

        self.assertTrue(wait_until(lambda: m.project_active_count("A") == 2))


# --------------------------------------------------------------------------- #
# Pause and schedule isolation
# --------------------------------------------------------------------------- #


class PauseIsolationTest(_Base):
    def test_a_paused_project_gets_no_new_work(self):
        m = self._manager(maxc=3, gates={"A": ProjectGate(paused=True)})
        self._add(m, "A", "a1")
        self._add(m, "A", "a2")

        m.start_all()

        time.sleep(0.2)
        self.assertEqual(m.project_active_count("A"), 0)
        self.assertEqual(self.runner.count_started(), 0)

    def test_pausing_one_project_does_not_stop_another(self):
        m = self._manager(maxc=3, gates={"A": ProjectGate(paused=True)})
        self._add(m, "A", "a1")
        self._add(m, "B", "b1")

        m.start_all()

        self.assertTrue(wait_until(lambda: m.project_active_count("B") == 1))
        self.assertEqual(m.project_active_count("A"), 0)

    def test_a_paused_project_does_not_stop_its_own_running_transfer(self):
        """The pause blocks *new* starts; it is not a stop button."""
        m = self._manager(maxc=2)
        self._add(m, "A", "a1")
        m.start_all()
        self.assertTrue(self._wait_active(m, 1))

        m.set_project_gates({"A": ProjectGate(paused=True)})

        time.sleep(0.15)
        self.assertEqual(m.project_active_count("A"), 1, "a running download must survive a pause")

    def test_resuming_a_project_starts_its_waiting_work(self):
        m = self._manager(maxc=3, gates={"A": ProjectGate(paused=True)})
        self._add(m, "A", "a1")
        m.start_all()
        time.sleep(0.15)
        self.assertEqual(m._active, 0)

        m.set_project_gates({"A": ProjectGate(paused=False)})

        self.assertTrue(self._wait_active(m, 1))


class ScheduleIsolationTest(_Base):
    def test_a_closed_window_blocks_only_its_own_project(self):
        m = self._manager(maxc=3, gates={"A": ProjectGate(window_open=False)})
        self._add(m, "A", "a1")
        self._add(m, "B", "b1")

        m.start_all()

        self.assertTrue(wait_until(lambda: m.project_active_count("B") == 1))
        self.assertEqual(m.project_active_count("A"), 0)

    def test_a_closed_window_does_not_stop_a_running_transfer(self):
        m = self._manager(maxc=2)
        self._add(m, "A", "a1")
        m.start_all()
        self.assertTrue(self._wait_active(m, 1))

        m.set_project_gates({"A": ProjectGate(window_open=False)})

        time.sleep(0.15)
        self.assertEqual(m.project_active_count("A"), 1)

    def test_opening_a_window_starts_the_waiting_work(self):
        m = self._manager(maxc=3, gates={"A": ProjectGate(window_open=False)})
        self._add(m, "A", "a1")
        m.start_all()
        time.sleep(0.15)
        self.assertEqual(m._active, 0)

        m.set_project_gates({"A": ProjectGate(window_open=True)})

        self.assertTrue(self._wait_active(m, 1))


class StarvationTest(_Base):
    def test_a_blocked_project_at_the_head_does_not_starve_the_ones_behind(self):
        """The queue must skip a blocked task, not stop at it."""
        m = self._manager(maxc=2, gates={"A": ProjectGate(paused=True)})
        # A's tasks are added first, so they occupy the head of the manual order.
        self._add(m, "A", "a1")
        self._add(m, "A", "a2")
        self._add(m, "A", "a3")
        self._add(m, "B", "b1")
        self._add(m, "B", "b2")

        m.start_all()

        self.assertTrue(wait_until(lambda: m.project_active_count("B") == 2))
        self.assertEqual(m.project_active_count("A"), 0)

    def test_a_closed_window_at_the_head_does_not_starve_the_ones_behind(self):
        m = self._manager(maxc=1, gates={"A": ProjectGate(window_open=False)})
        self._add(m, "A", "a1")
        self._add(m, "B", "b1")

        m.start_all()

        self.assertTrue(wait_until(lambda: m.project_active_count("B") == 1))
        self.assertEqual(m.project_active_count("A"), 0)

    def test_every_runnable_task_eventually_starts(self):
        m = self._manager(maxc=2, gates={"A": ProjectGate(paused=True)})
        self._add(m, "A", "a1")
        for i in range(3):
            self._add(m, "B", f"b{i}")

        m.start_all()
        self.assertTrue(wait_until(lambda: m.project_active_count("B") == 2))

        self.runner.release_all()
        self.assertTrue(wait_until(lambda: self.runner.count_started() == 3, timeout=10))


# --------------------------------------------------------------------------- #
# Worker hygiene
# --------------------------------------------------------------------------- #


class WorkerHygieneTest(_Base):
    def test_repeated_gate_pushes_do_not_duplicate_a_worker(self):
        m = self._manager(maxc=2)
        self._add(m, "A", "a1")
        m.start_all()
        self.assertTrue(self._wait_active(m, 1))

        for _ in range(25):
            m.set_project_gates({"A": ProjectGate(limit=5)})

        time.sleep(0.2)
        self.assertEqual(m._active, 1, "a second worker was spawned for the same task")
        self.assertEqual(m.project_active_count("A"), 1)
        self.assertEqual(self.runner.count_started(), 1)

    def test_a_slot_is_released_when_a_transfer_finishes(self):
        m = self._manager(maxc=2)
        self._add(m, "A", "a1")
        m.start_all()
        self.assertTrue(self._wait_active(m, 1))

        self.runner.release_all()

        self.assertTrue(wait_until(lambda: m._active == 0))
        self.assertEqual(m.project_active_count("A"), 0)
        self.assertEqual(m.project_active_counts(), {}, "zero entries must be pruned")

    def test_a_slot_is_released_when_a_task_is_cancelled(self):
        m = self._manager(maxc=2)
        task_id = self._add(m, "A", "a1")
        m.start_all()
        self.assertTrue(self._wait_active(m, 1))

        m.cancel_task(task_id)

        self.assertTrue(wait_until(lambda: m._active == 0))
        self.assertEqual(m.project_active_count("A"), 0)

    def test_the_counts_stay_consistent_after_a_full_pass(self):
        m = self._manager(maxc=2, gates={"A": ProjectGate(limit=1)})
        for i in range(3):
            self._add(m, "A", f"a{i}")
        for i in range(2):
            self._add(m, "B", f"b{i}")
        m.start_all()

        self.assertTrue(self._wait_active(m, 2))
        self.assertEqual(m.project_active_count("A"), 1)
        self.assertEqual(m.project_active_count("B"), 1)
        self.assertEqual(sum(m.project_active_counts().values()), m._active)

        self.runner.release_all()
        self.assertTrue(wait_until(lambda: m._active == 0, timeout=10))
        self.assertEqual(m.project_active_counts(), {})


# --------------------------------------------------------------------------- #
# Admission reporting and task moves
# --------------------------------------------------------------------------- #


class AdmissionReportTest(_Base):
    def test_an_unknown_project_is_treated_as_open(self):
        """Blocking on missing information would stall the whole queue."""
        m = self._manager(maxc=2)
        task_id = self._add(m, "ghost-project", "g1")
        self.assertEqual(m.project_admission(task_id)["allowed"], True)

    def test_a_paused_project_reports_the_reason(self):
        m = self._manager(maxc=2, gates={"A": ProjectGate(paused=True)})
        task_id = self._add(m, "A", "a1")
        admission = m.project_admission(task_id)
        self.assertFalse(admission["allowed"])
        self.assertEqual(admission["reason"], "project_paused")

    def test_a_full_pool_reports_the_global_limit(self):
        m = self._manager(maxc=1)
        self._add(m, "A", "a1")
        second = self._add(m, "A", "a2")
        m.start_all()
        self.assertTrue(self._wait_active(m, 1))

        admission = m.project_admission(second)
        self.assertFalse(admission["allowed"])
        self.assertEqual(admission["reason"], "global_limit")

    def test_an_unknown_task_reports_unknown(self):
        m = self._manager()
        self.assertEqual(m.project_admission("nope")["reason"], "unknown_task")

    def test_a_project_limit_is_reported_when_a_global_slot_is_free(self):
        m = self._manager(maxc=3, gates={"A": ProjectGate(limit=1)})
        self._add(m, "A", "a1")
        second = self._add(m, "A", "a2")
        m.start_all()
        self.assertTrue(self._wait_active(m, 1))

        admission = m.project_admission(second)
        self.assertFalse(admission["allowed"])
        self.assertEqual(admission["reason"], "project_limit")


class TaskMoveTest(_Base):
    def test_moving_a_task_relabels_it_without_disturbing_it(self):
        m = self._manager(maxc=2)
        task_id = self._add(m, "A", "a1")
        m.start_all()
        self.assertTrue(self._wait_active(m, 1))

        self.assertTrue(m.set_task_project(task_id, "B"))

        snap = next(s for s in m.snapshots() if s.id == task_id)
        self.assertEqual(snap.project_id, "B")
        self.assertIn(snap.state, ACTIVE_STATES, "a move must not disturb the transfer")
        self.assertNotEqual(snap.state, TaskStatus.QUEUED, "the task must not be re-queued")

    def test_moving_an_unknown_task_is_a_no_op(self):
        m = self._manager()
        self.assertFalse(m.set_task_project("nope", "B"))

    def test_an_empty_project_id_falls_back_to_the_default(self):
        m = self._manager()
        task_id = self._add(m, "A", "a1")
        m.set_task_project(task_id, "")
        snap = next(s for s in m.snapshots() if s.id == task_id)
        self.assertEqual(snap.project_id, DEFAULT_PROJECT_ID)

    def test_a_moved_task_is_charged_to_its_new_project_when_it_starts(self):
        m = self._manager(maxc=2, gates={"B": ProjectGate(limit=1)})
        task_id = self._add(m, "A", "a1")
        m.set_task_project(task_id, "B")
        m.start_all()
        self.assertTrue(self._wait_active(m, 1))
        self.assertEqual(m.project_active_count("B"), 1)
        self.assertEqual(m.project_active_count("A"), 0)

    def test_the_project_id_survives_a_snapshot_round_trip(self):
        m = self._manager()
        task_id = self._add(m, "A", "a1")
        snap = next(s for s in m.snapshots() if s.id == task_id)
        self.assertEqual(snap.to_dict()["project_id"], "A")


class PersistenceTest(_Base):
    def test_project_id_survives_a_restart(self):
        m = self._manager(maxc=1)
        self._add(m, "A", "a1")
        m.close()

        self.manager = TaskManager(self.runner, self.tmp, max_concurrent=1)
        snaps = self.manager.snapshots()
        self.assertEqual([s.project_id for s in snaps], ["A"])

    def test_gates_are_runtime_only_and_not_persisted(self):
        """A gate is live scheduling state; it must not leak into the database."""
        m = self._manager(maxc=1, gates={"A": ProjectGate(paused=True)})
        self._add(m, "A", "a1")
        m.close()

        self.manager = TaskManager(self.runner, self.tmp, max_concurrent=1)
        self.assertEqual(self.manager.project_gates, {})


if __name__ == "__main__":
    unittest.main()
