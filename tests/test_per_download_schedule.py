"""Per-download start/end windows and the download-scoped shutdown request.

Two layers are covered:

* the pure daily-window arithmetic in :mod:`core.scheduler`, including the
  ``00:00`` "not configured" sentinel;
* the queue integration — a scheduled task stays queued, an explicit Start does
  not bypass it, an end boundary pauses resumably, and the persisted fields
  survive a restart.

Shutdown is exercised through the **existing** ``AutoShutdownController`` with
:class:`FakePowerController`; no test in this file can power off the machine.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.auto_shutdown import (
    AutoShutdownController,
    FakePowerController,
    ManualTimerFactory,
    ShutdownState,
)
from core.db import Database
from core.migrations import MIGRATIONS, SCHEMA_VERSION, table_columns
from core.scheduler import (
    normalize_schedule_time,
    normalize_task_schedule,
    task_window_open,
)
from core.session import SessionManager
from core.task import DownloadTask, TaskStatus
from ui.api import Api
from ui.common import DownloadRequest, TaskManager

SIZE = 100


def wait_until(cond, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _future_closed_window() -> tuple[str, str]:
    """A two-hour window that starts tomorrow-ish and is closed right now."""
    now = datetime.now()
    start = (now + timedelta(hours=1)).strftime("%H:%M")
    end = (now + timedelta(hours=2)).strftime("%H:%M")
    return start, end


def _window_around_now() -> tuple[str, str]:
    """A window that contains the current minute (overnight-safe)."""
    now = datetime.now()
    start = (now - timedelta(hours=1)).strftime("%H:%M")
    end = (now + timedelta(hours=2)).strftime("%H:%M")
    return start, end


class BlockingRunner:
    """Analyze instantly, then hold DOWNLOADING until cancelled/resumed."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.last_error = ""

    def analyze(self, task_id, request, control):
        return SimpleNamespace(
            ok=True,
            total_size=SIZE,
            supports_range=True,
            filename="file.zip",
            content_type="application/zip",
            server="test",
            etag="",
            last_modified="",
        )

    def download(self, task_id, request, analysis, progress, control,
                 status_callback=None, path_callback=None, smart_callback=None):
        self.started.append(task_id)
        if status_callback:
            status_callback("DOWNLOADING")
        while not control.cancelled:
            control.wait_if_paused()
        return False


class FakeManager:
    """Minimal queue view for the shutdown-controller tests."""

    def __init__(self, states=()) -> None:
        self.states = list(states)

    def snapshots(self):
        return [SimpleNamespace(state=s) for s in self.states]

    def set(self, *states) -> None:
        self.states = list(states)


# ── Pure window arithmetic ──────────────────────────────────────────────────


class ScheduleTimeTest(unittest.TestCase):
    def test_00_00_is_the_unset_sentinel(self):
        self.assertEqual(normalize_schedule_time("00:00"), "")
        self.assertEqual(normalize_schedule_time("0:00"), "")
        self.assertEqual(normalize_task_schedule("00:00", "00:00"), ("", ""))

    def test_times_are_canonicalised(self):
        self.assertEqual(normalize_schedule_time("9:5"), "09:05")
        self.assertEqual(normalize_schedule_time("23:59"), "23:59")

    def test_invalid_times_are_rejected(self):
        for bad in ("24:00", "12:60", "noon", "12", "12:34:56", True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    normalize_schedule_time(bad)

    def test_equal_boundaries_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_task_schedule("09:00", "09:00")

    def test_no_window_is_always_open(self):
        self.assertTrue(task_window_open("", "", dtime(12, 0)))

    def test_start_only_waits_until_start(self):
        self.assertFalse(task_window_open("09:00", "", dtime(8, 59)))
        self.assertTrue(task_window_open("09:00", "", dtime(9, 0)))
        self.assertTrue(task_window_open("09:00", "", dtime(23, 59)))

    def test_end_only_closes_at_end(self):
        self.assertTrue(task_window_open("", "17:00", dtime(16, 59)))
        self.assertFalse(task_window_open("", "17:00", dtime(17, 0)))

    def test_both_boundaries_and_overnight_wrap(self):
        self.assertTrue(task_window_open("09:00", "17:00", dtime(12, 0)))
        self.assertFalse(task_window_open("09:00", "17:00", dtime(18, 0)))
        # 23:00 -> 07:00 wraps midnight, matching the project scheduler.
        self.assertTrue(task_window_open("23:00", "07:00", dtime(3, 0)))
        self.assertFalse(task_window_open("23:00", "07:00", dtime(12, 0)))


# ── Persistence ─────────────────────────────────────────────────────────────


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "n13.db"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_task_round_trips_schedule_and_shutdown(self):
        db = Database(self.db_path)
        try:
            task = DownloadTask(
                url="https://example.com/a.zip",
                directory=str(self.tmp),
                label="a.zip",
                schedule_start="09:00",
                schedule_end="17:00",
                shutdown_when_done=True,
            )
            from core.store import TaskStore

            store = TaskStore(db)
            store.save_task(task.to_dict())
            restored = DownloadTask.from_dict(store.load_task(task.id))
            self.assertIn("schedule_start", table_columns(db._conn, "tasks"))
            self.assertIn("schedule_end", table_columns(db._conn, "tasks"))
            self.assertIn("shutdown_when_done", table_columns(db._conn, "tasks"))
            self.assertEqual(restored.schedule_start, "09:00")
            self.assertEqual(restored.schedule_end, "17:00")
            self.assertTrue(restored.shutdown_when_done)
        finally:
            db.close()

    def test_pre_v4_rows_upgrade_with_empty_defaults(self):
        # Build a v3 database by replaying the real ledger, then let Database
        # apply v4.  Existing downloads must be untouched.
        conn = sqlite3.connect(str(self.db_path))
        try:
            for version, _description, migrate in MIGRATIONS:
                if version > 3:
                    break
                migrate(conn)
            conn.execute("PRAGMA user_version=3")
            conn.execute(
                "INSERT INTO tasks (id, url, created_at) VALUES (?, ?, ?)",
                ("legacy", "https://example.com/old.zip", time.time()),
            )
            conn.commit()
        finally:
            conn.close()

        db = Database(self.db_path)
        try:
            self.assertEqual(db.schema_version(), SCHEMA_VERSION)
            row = db.query_one("SELECT * FROM tasks WHERE id='legacy'")
            self.assertEqual(row["schedule_start"], "")
            self.assertEqual(row["schedule_end"], "")
            self.assertEqual(int(row["shutdown_when_done"]), 0)
            task = DownloadTask.from_dict(row)
            self.assertEqual(task.schedule_start, "")
            self.assertEqual(task.schedule_end, "")
            self.assertFalse(task.shutdown_when_done)
        finally:
            db.close()

    def test_schedule_survives_a_task_manager_restart(self):
        first = TaskManager(BlockingRunner(), self.tmp, max_concurrent=1)
        try:
            task_id = first.add(
                DownloadRequest(
                    url="https://example.com/restart.zip",
                    directory=str(self.tmp),
                    label="restart.zip",
                    schedule_start="22:00",
                    schedule_end="06:00",
                    shutdown_when_done=True,
                ),
                autostart=False,
            )
        finally:
            first.shutdown(cancel=True, wait=True)
            first.close()

        second = TaskManager(BlockingRunner(), self.tmp, max_concurrent=1)
        try:
            snap = second.get(task_id)
            self.assertIsNotNone(snap)
            self.assertEqual(snap.schedule_start, "22:00")
            self.assertEqual(snap.schedule_end, "06:00")
            self.assertTrue(snap.shutdown_when_done)
        finally:
            second.shutdown(cancel=True, wait=True)
            second.close()


# ── Queue integration ───────────────────────────────────────────────────────


class QueueWindowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.runner = BlockingRunner()
        self.manager = TaskManager(self.runner, self.tmp, max_concurrent=1)

    def tearDown(self):
        self.manager.shutdown(cancel=True, wait=True)
        self.manager.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_autostart_does_not_bypass_a_closed_window(self):
        start, end = _future_closed_window()
        task_id = self.manager.add(
            DownloadRequest(
                url="https://example.com/closed.zip",
                directory=str(self.tmp),
                label="closed.zip",
                schedule_start=start,
                schedule_end=end,
            ),
            autostart=True,
        )
        time.sleep(0.15)
        self.assertEqual(self.manager.get(task_id).state, TaskStatus.QUEUED)
        self.assertEqual(self.runner.started, [])

    def test_explicit_start_does_not_bypass_a_closed_window(self):
        start, end = _future_closed_window()
        task_id = self.manager.add(
            DownloadRequest(
                url="https://example.com/explicit.zip",
                directory=str(self.tmp),
                label="explicit.zip",
                schedule_start=start,
                schedule_end=end,
            ),
            autostart=False,
        )
        self.manager.start_task(task_id)
        time.sleep(0.15)
        self.assertEqual(self.manager.get(task_id).state, TaskStatus.QUEUED)
        self.assertEqual(self.runner.started, [])

    def test_opening_window_starts_a_queued_task(self):
        task_id = self.manager.add(
            DownloadRequest(
                url="https://example.com/open-later.zip",
                directory=str(self.tmp),
                label="open-later.zip",
                schedule_start="11:00",
                schedule_end="13:00",
            ),
            autostart=False,
        )
        self.assertEqual(self.manager.get(task_id).state, TaskStatus.QUEUED)
        with patch("core.scheduler._now_time", return_value=dtime(12, 0)):
            self.manager.enforce_schedule_windows()
        self.assertTrue(wait_until(
            lambda: self.manager.get(task_id).state == TaskStatus.DOWNLOADING))

    def test_end_boundary_pauses_resumably_and_start_reopens(self):
        start, end = _window_around_now()
        task_id = self.manager.add(
            DownloadRequest(
                url="https://example.com/pause.zip",
                directory=str(self.tmp),
                label="pause.zip",
                schedule_start=start,
                schedule_end=end,
            ),
            autostart=True,
        )
        self.assertTrue(wait_until(
            lambda: self.manager.get(task_id).state == TaskStatus.DOWNLOADING))
        self.assertIn(task_id, self.runner.started)

        # Four hours past now is outside the three-hour window.
        outside = (datetime.now() + timedelta(hours=4)).time().replace(microsecond=0)
        self.manager.enforce_schedule_windows(now=outside)
        self.assertEqual(self.manager.get(task_id).state, TaskStatus.PAUSED)
        self.assertTrue(self.manager._tasks[task_id].control.paused)

        inside = datetime.now().time().replace(microsecond=0)
        self.manager.enforce_schedule_windows(now=inside)
        self.assertTrue(wait_until(
            lambda: self.manager.get(task_id).state == TaskStatus.DOWNLOADING))
        self.assertFalse(self.manager._tasks[task_id].control.paused)

    def test_start_only_never_interrupts_a_running_transfer(self):
        # The current minute is always inside a start-only window (now >= start).
        start = datetime.now().strftime("%H:%M")
        task_id = self.manager.add(
            DownloadRequest(
                url="https://example.com/start-only.zip",
                directory=str(self.tmp),
                label="start-only.zip",
                schedule_start=start,
            ),
            autostart=True,
        )
        self.assertTrue(wait_until(
            lambda: self.manager.get(task_id).state == TaskStatus.DOWNLOADING))
        # Even a "closed" instant for a start-only task (before the start on a
        # wrapped clock) must not cut a transfer that was already allowed.
        self.manager.enforce_schedule_windows(now=dtime(0, 0))
        self.assertEqual(self.manager.get(task_id).state, TaskStatus.DOWNLOADING)


# ── Shutdown precedence and safety ──────────────────────────────────────────


class DownloadShutdownTest(unittest.TestCase):
    def make_controller(self, states, cfg=None):
        self.cfg = cfg if cfg is not None else AppConfig()
        self.manager = FakeManager(states)
        self.power = FakePowerController()
        self.timers = ManualTimerFactory()
        self.controller = AutoShutdownController(
            self.manager,
            self.cfg,
            power=self.power,
            timer_factory=self.timers,
            state_file=None,
        )
        return self.controller

    def test_arming_waits_for_every_download(self):
        c = self.make_controller([TaskStatus.DOWNLOADING, TaskStatus.QUEUED])
        c.arm_for_download_completion("task-a")
        self.assertEqual(self.power.schedule_count, 0)
        self.assertEqual(c.status()["state"], ShutdownState.WAITING.value)
        self.assertTrue(c.status()["session_arm"])
        self.assertEqual(c.status()["armed_by_task"], "task-a")
        self.assertFalse(self.cfg.shutdown_when_done)

        self.manager.set(TaskStatus.COMPLETED, TaskStatus.QUEUED)
        c.notify("finished")
        self.assertEqual(self.power.schedule_count, 0)

        self.manager.set(TaskStatus.COMPLETED, TaskStatus.DOWNLOADING)
        c.notify("started")
        self.assertEqual(self.power.schedule_count, 0)

        self.manager.set(TaskStatus.COMPLETED, TaskStatus.COMPLETED)
        c.notify("finished")
        self.assertEqual(self.power.schedule_count, 1)
        self.assertEqual(c.status()["state"], ShutdownState.COUNTDOWN.value)

    def test_failure_cancellation_and_pause_block_the_shutdown(self):
        for blocking in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.PAUSED):
            with self.subTest(blocking=blocking):
                c = self.make_controller([TaskStatus.COMPLETED, blocking])
                c.arm_for_download_completion("task-a")
                self.assertEqual(self.power.schedule_count, 0)
                self.assertNotEqual(
                    c.status()["state"], ShutdownState.COUNTDOWN.value
                )

    def test_duplicate_events_produce_one_countdown(self):
        c = self.make_controller([TaskStatus.COMPLETED, TaskStatus.COMPLETED])
        c.arm_for_download_completion("task-a")
        for _ in range(20):
            c.notify("finished")
        self.assertEqual(self.power.schedule_count, 1)
        self.assertLessEqual(self.timers.live_count, 1)

    def test_global_off_wins_over_a_download_request(self):
        c = self.make_controller([TaskStatus.DOWNLOADING])
        c.arm_for_download_completion("task-a")
        c.set_enabled(False)
        self.assertEqual(c.status()["state"], ShutdownState.DISABLED.value)
        self.assertFalse(c.status()["session_arm"])


# ── API wiring ──────────────────────────────────────────────────────────────


class ApiScheduleTest(unittest.TestCase):
    """The bridge layer: validation, persistence and controller wiring."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [
            patch("core.paths.data_dir", return_value=self.tmp / "data"),
            patch("core.paths.config_dir", return_value=self.tmp / "config"),
            patch("ui.api.config_dir", return_value=self.tmp / "config"),
            patch("ui.api.save_config"),
            patch("core.startup.drain", return_value=[]),
        ]
        for p in self.patches:
            p.start()
        self.power = FakePowerController()
        self.cfg = AppConfig()
        self.cfg.auto_start_server = False
        self.cfg.resume_on_startup = False
        self.cfg.download_dir = str(self.tmp / "downloads")
        self.cfg.connection_mode = "manual"
        self.api = self._make_api(self.cfg)

    def _make_api(self, cfg):
        return Api(cfg, SessionManager(cfg), auto_shutdown_power=self.power)

    def tearDown(self):
        for api in getattr(self, "_apis", [self.api]):
            try:
                api.shutdown()
            except Exception:
                pass
        for p in reversed(self.patches):
            p.stop()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_00_00_is_not_a_schedule(self):
        tid = self.api.add_download(
            "https://example.com/unset.zip",
            directory=str(self.tmp),
            autostart=False,
            schedule_start="00:00",
            schedule_end="00:00",
        )
        self.assertTrue(tid)
        snap = self.api._manager.get(tid)
        self.assertEqual(snap.schedule_start, "")
        self.assertEqual(snap.schedule_end, "")

    def test_invalid_and_equal_times_are_rejected_safely(self):
        for start, end in (("25:00", ""), ("", "12:60"), ("09:00", "09:00")):
            with self.subTest(start=start, end=end):
                tid = self.api.add_download(
                    "https://example.com/bad.zip",
                    directory=str(self.tmp),
                    autostart=False,
                    schedule_start=start,
                    schedule_end=end,
                )
                self.assertEqual(tid, "")
        self.assertEqual(len(self.api._manager.snapshots()), 0)

    def test_times_are_canonicalised_and_persisted(self):
        tid = self.api.add_download(
            "https://example.com/canon.zip",
            directory=str(self.tmp),
            autostart=False,
            schedule_start="9:5",
            schedule_end="17:0",
        )
        snap = self.api._manager.get(tid)
        self.assertEqual(snap.schedule_start, "09:05")
        self.assertEqual(snap.schedule_end, "17:00")
        self.assertEqual(snap.to_dict()["schedule_start"], "09:05")

    def test_start_immediately_cannot_bypass_a_closed_window(self):
        start, end = _future_closed_window()
        tid = self.api.add_download(
            "https://example.com/wait.zip",
            directory=str(self.tmp),
            autostart=True,
            schedule_start=start,
            schedule_end=end,
        )
        time.sleep(0.15)
        self.assertEqual(self.api._manager.get(tid).state, TaskStatus.QUEUED)

    def test_shutdown_option_arms_the_existing_controller_only_when_checked(self):
        plain = self.api.add_download(
            "https://example.com/plain.zip",
            directory=str(self.tmp),
            autostart=False,
        )
        self.assertTrue(plain)
        status = self.api.get_auto_shutdown_status()
        self.assertFalse(status["session_arm"])
        self.assertEqual(status["state"], ShutdownState.DISABLED.value)
        self.assertEqual(self.power.schedule_count, 0)

        armed_tid = self.api.add_download(
            "https://example.com/armed.zip",
            directory=str(self.tmp),
            autostart=False,
            shutdown_when_done=True,
        )
        status = self.api.get_auto_shutdown_status()
        self.assertTrue(status["session_arm"])
        self.assertEqual(status["armed_by_task"], armed_tid)
        self.assertEqual(status["state"], ShutdownState.WAITING.value)
        # Per-download request must not silently persist the global preference.
        self.assertFalse(self.cfg.shutdown_when_done)
        self.assertEqual(self.power.schedule_count, 0)

    def test_cancel_clears_the_per_download_request(self):
        tid = self.api.add_download(
            "https://example.com/cancel.zip",
            directory=str(self.tmp),
            autostart=False,
            shutdown_when_done=True,
        )
        self.assertTrue(self.api._manager.get(tid).shutdown_when_done)
        self.api.cancel_auto_shutdown()
        self.assertFalse(self.api._manager.get(tid).shutdown_when_done)
        self.assertFalse(self.api.get_auto_shutdown_status()["session_arm"])

    def test_restart_rearms_an_unfinished_download_request(self):
        tid = self.api.add_download(
            "https://example.com/restart-api.zip",
            directory=str(self.tmp),
            autostart=False,
            schedule_start="22:00",
            schedule_end="06:00",
            shutdown_when_done=True,
        )
        self.api.shutdown()

        cfg2 = AppConfig()
        cfg2.auto_start_server = False
        cfg2.resume_on_startup = False
        cfg2.download_dir = str(self.tmp / "downloads")
        cfg2.connection_mode = "manual"
        second = self._make_api(cfg2)
        self._apis = [second]
        try:
            status = second.get_auto_shutdown_status()
            self.assertTrue(status["session_arm"])
            self.assertEqual(status["armed_by_task"], tid)
            self.assertFalse(cfg2.shutdown_when_done)
            snap = second._manager.get(tid)
            self.assertEqual(snap.schedule_start, "22:00")
            self.assertEqual(snap.schedule_end, "06:00")
            self.assertTrue(snap.shutdown_when_done)
        finally:
            second.shutdown()


if __name__ == "__main__":
    unittest.main()
