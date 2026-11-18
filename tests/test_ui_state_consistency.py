"""Regression tests for verified pause/cancel/remove and project-deletion bugs.

These guard the state-machine fixes from the 2026-11 UI/UX overhaul:

* pausing a task that is active but not yet ``DOWNLOADING`` used to leave its
  control paused forever (Resume keyed only on the ``PAUSED`` status);
* cancelling/removing a restored (worker-less) ``PAUSED`` task relied on a
  worker that does not exist, so no terminal event fired / the row never left;
* deleting a project edited only the DB, leaving the in-memory registry stale;
* a paused task was not counted as live work, so a project holding one could be
  deleted underneath a still-parked worker.

All tests use a deterministic fake runner and never touch the network.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import DEFAULT_PROJECT_ID, Database
from core.store import TaskStore
from core.task import DownloadTask, TaskStatus
from projects.models import ProjectValidationError
from projects.service import ProjectService
from ui.common import DownloadRequest, TaskManager

SIZE = 100


class FakeRunner:
    def __init__(self) -> None:
        self.last_error = ""

    def analyze(self, task_id, request, control):
        return SimpleNamespace(
            ok=True,
            total_size=SIZE,
            supports_range=True,
            filename="a.zip",
            content_type="application/zip",
            server="test",
            etag="",
            last_modified="",
        )

    def download(
        self, task_id, request, analysis, progress, control,
        status_callback=None, path_callback=None, smart_callback=None,
    ):
        if status_callback:
            status_callback("DOWNLOADING")
        progress(SIZE, SIZE)
        return True


class _ManagerBase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.manager = TaskManager(FakeRunner(), Path(self.dir), max_concurrent=2)
        self.events: list[tuple[str, object]] = []
        self.manager.subscribe(lambda ev, snap: self.events.append((ev, snap)))
        self.tid = self.manager.add(
            DownloadRequest(
                url="https://example.com/a.zip",
                directory=self.dir,
                label="a.zip",
            ),
            autostart=False,  # keep it deterministic; no worker races
        )

    def tearDown(self) -> None:
        try:
            self.manager.close()
        except Exception:
            pass

    def _rec(self):
        return self.manager._tasks[self.tid]


class PauseOutsideDownloadingTests(_ManagerBase):
    def test_resume_releases_control_paused_during_analyzing(self):
        rec = self._rec()
        rec.task.force_status(TaskStatus.ANALYZING)
        self.manager.pause_task(self.tid)
        self.assertTrue(rec.control.paused)
        # ANALYZING has no legal transition to PAUSED — the status must stay.
        self.assertEqual(rec.task.status, TaskStatus.ANALYZING)

        self.manager.resume_task(self.tid)
        self.assertFalse(
            rec.control.paused,
            "Resume must release a control paused before DOWNLOADING, otherwise "
            "the worker parks at the next chunk barrier with no way out",
        )

    def test_resume_releases_control_paused_while_status_is_downloading(self):
        # A pause issued during STARTING/ANALYZING can reach the download loop
        # before the user hits Resume; the status is then DOWNLOADING while the
        # control is still paused.  Resume used to be a no-op here.
        rec = self._rec()
        rec.task.force_status(TaskStatus.DOWNLOADING)
        rec.control.pause()
        self.manager.resume_task(self.tid)
        self.assertFalse(rec.control.paused)

    def test_resume_all_releases_paused_controls_outside_paused_status(self):
        rec = self._rec()
        rec.task.force_status(TaskStatus.MERGING)
        rec.control.pause()
        self.manager.resume_all()
        self.assertFalse(rec.control.paused)


class WorkerlessTerminalTests(_ManagerBase):
    def _make_restored_pause(self):
        rec = self._rec()
        rec.task.force_status(TaskStatus.PAUSED)
        return rec

    def test_cancel_workerless_paused_emits_finished(self):
        self._make_restored_pause()
        self.events.clear()
        self.manager.cancel_task(self.tid)
        kinds = [ev for ev, _ in self.events]
        self.assertIn("finished", kinds)
        snap = self.manager.get(self.tid)
        self.assertEqual(snap.state, TaskStatus.CANCELLED)
        self.assertIsNotNone(snap.finished_at)

    def test_remove_workerless_paused_deletes_record_and_emits_removed(self):
        self._make_restored_pause()
        self.events.clear()
        self.manager.remove_task(self.tid)
        self.assertIsNone(self.manager.get(self.tid))
        self.assertEqual([ev for ev, _ in self.events], ["removed"])
        self.assertIsNone(self.manager._store.load_task(self.tid))


class ProjectDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "n13.db")
        self.store = TaskStore(self.db)
        self.service = ProjectService(self.db, self.store)
        self.service.ensure_default()
        self.project = self.service.create("ToDelete")
        self.pid = self.project.id

    def tearDown(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass

    def _task(self, status: str) -> DownloadTask:
        return DownloadTask(
            id="t1",
            url="https://x/t1.zip",
            directory="C:/dl",
            filename="t1.zip",
            category="Archives",
            status=status,
            total_size=1000,
            downloaded_size=250,
            project_id=self.pid,
        )

    def _save(self, status: str) -> None:
        payload = self._task(status).to_dict()
        payload["project_id"] = self.pid
        self.store.save_task(payload)

    def test_paused_task_blocks_project_deletion(self):
        self._save("Paused")
        self.assertTrue(self.service.has_active_tasks(self.pid))
        with self.assertRaises(ProjectValidationError) as ctx:
            self.service.delete(self.pid, "delete_records")
        self.assertEqual(ctx.exception.code, "project_busy")

    def test_running_task_still_blocks_project_deletion(self):
        self._save("Downloading")
        self.assertTrue(self.service.has_active_tasks(self.pid))


class ApplyProjectDeletionTests(_ManagerBase):
    """The manager half of a deletion: in-memory registry follows the DB."""

    def test_keep_tasks_reassigns_in_memory(self):
        rec = self._rec()
        rec.task.project_id = "doomed"
        self.manager.apply_project_deletion("doomed", "keep_tasks")
        self.assertEqual(rec.task.project_id, DEFAULT_PROJECT_ID)
        self.assertEqual(self.manager.get(self.tid).project_id, DEFAULT_PROJECT_ID)

    def test_delete_records_removes_from_memory_and_emits(self):
        rec = self._rec()
        rec.task.project_id = "doomed"
        self.events.clear()
        self.manager.apply_project_deletion("doomed", "delete_records")
        self.assertIsNone(self.manager.get(self.tid))
        self.assertEqual([ev for ev, _ in self.events], ["removed"])


if __name__ == "__main__":
    unittest.main()
