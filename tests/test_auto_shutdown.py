"""Auto shutdown — subsystem tests.

Covers the state machine, the eligibility policy, the Windows power
abstraction, the countdown race conditions, crash recovery and the
safety invariants.

Every test injects :class:`FakePowerController` (and usually a manual timer
factory), so **no test in this file can power off the machine**.  That is the
whole point of the abstraction: the highest-impact operation in the product is
exercised for real, minus the side effect.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.auto_shutdown import (
    DEFAULT_COUNTDOWN_SECONDS,
    FakePowerController,
    ManualTimerFactory,
    PowerError,
    PowerUnsupportedError,
    QueueState,
    ShutdownReason,
    ShutdownState,
    WindowsPowerController,
    AutoShutdownController,
    AutoShutdownPolicy,
    SchedulerSnapshot,
    real_shutdown_allowed,
)
from core.task import TaskStatus
from ui.common import DownloadRequest, TaskManager

# ── Test doubles ────────────────────────────────────────────────────────────


class FakeClock:
    """Deterministic stand-in for ``time.time``.

    Starts at the real wall clock so comparisons against real datetimes (the
    one-off ``schedule_time``) stay meaningful.
    """

    def __init__(self, start: float | None = None) -> None:
        self.now = time.time() if start is None else float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class FakeManager:
    """Minimal ``TaskManager`` surface: ``snapshots()``."""

    def __init__(self, states=()) -> None:
        self.states = list(states)
        self.raise_on_snapshots = False

    def snapshots(self):
        if self.raise_on_snapshots:
            raise RuntimeError("queue unreadable")
        return [SimpleNamespace(state=s) for s in self.states]

    def set(self, *states) -> None:
        self.states = list(states)


class Harness:
    """A controller wired to test doubles, with convenient accessors."""

    def __init__(self, states=(), *, cfg=None, power=None, timers=None,
                 scheduler=None, persist=None, state_file=None, clock=None,
                 countdown_grace=None):
        self.cfg = cfg if cfg is not None else AppConfig()
        self.manager = FakeManager(states)
        self.power = power if power is not None else FakePowerController()
        self.timers = timers if timers is not None else ManualTimerFactory()
        self.clock = clock if clock is not None else FakeClock()
        self.persisted = 0
        kwargs = {}
        if countdown_grace is not None:
            kwargs["countdown_grace"] = countdown_grace
        self.controller = AutoShutdownController(
            self.manager,
            self.cfg,
            power=self.power,
            timer_factory=self.timers,
            scheduler=scheduler,
            persist=persist or self._persist,
            state_file=state_file,
            clock=self.clock,
            **kwargs,
        )

    def _persist(self) -> None:
        self.persisted += 1

    # -- shortcuts ---------------------------------------------------------

    @property
    def state(self) -> str:
        return self.controller.state.value

    @property
    def status(self):
        return self.controller.status()

    def arm(self):
        self.cfg.shutdown_when_done = True
        return self.controller.set_enabled(True)

    def notify(self, event, snapshot=None):
        return self.controller.notify(event, snapshot)

    def finish_all(self, count=1):
        self.manager.set(*([TaskStatus.COMPLETED] * count))


def completed(n=1):
    return [TaskStatus.COMPLETED] * n


# ── Policy ──────────────────────────────────────────────────────────────────


class PolicyTest(unittest.TestCase):
    """The explicit "what does done mean?" rules."""

    def setUp(self):
        self.policy = AutoShutdownPolicy()
        self.now = 1_000_000.0

    def decide(self, **counts):
        return self.policy.evaluate(QueueState(**counts), now=self.now)

    def test_finished_work_is_eligible(self):
        d = self.decide(completed=3)
        self.assertTrue(d.eligible)
        self.assertEqual(d.reason, ShutdownReason.NONE)

    def test_active_blocks(self):
        for state_counts in ({"active": 1}, {"active": 1, "completed": 5}):
            d = self.decide(**state_counts)
            self.assertFalse(d.eligible)
            self.assertEqual(d.reason, ShutdownReason.ACTIVE_TASK)

    def test_queued_blocks(self):
        d = self.decide(queued=2, completed=1)
        self.assertFalse(d.eligible)
        self.assertEqual(d.reason, ShutdownReason.QUEUED_TASK)

    def test_queued_under_scheduler_gate_reports_schedule_reason(self):
        d = self.decide(queued=1, scheduler_gated=True)
        self.assertFalse(d.eligible)
        self.assertEqual(d.reason, ShutdownReason.SCHEDULED_TASK_PENDING)

    def test_paused_blocks(self):
        d = self.decide(paused=1, completed=1)
        self.assertFalse(d.eligible)
        self.assertEqual(d.reason, ShutdownReason.PAUSED_TASK)

    def test_failed_blocks_by_default(self):
        d = self.decide(completed=1, failed=1)
        self.assertFalse(d.eligible)
        self.assertEqual(d.reason, ShutdownReason.TASK_FAILED)

    def test_failed_allowed_when_policy_says_so(self):
        policy = AutoShutdownPolicy(allow_failures=True)
        d = policy.evaluate(QueueState(completed=1, failed=1), now=self.now)
        self.assertTrue(d.eligible)

    def test_cancelled_blocks_by_default(self):
        d = self.decide(completed=1, cancelled=1)
        self.assertFalse(d.eligible)
        self.assertEqual(d.reason, ShutdownReason.TASK_CANCELLED)

    def test_cancelled_allowed_when_policy_says_so(self):
        policy = AutoShutdownPolicy(allow_cancelled=True)
        d = policy.evaluate(QueueState(completed=1, cancelled=1), now=self.now)
        self.assertTrue(d.eligible)

    def test_empty_queue_is_not_eligible(self):
        """Arming with nothing to wait for must never power the machine off."""
        d = self.decide()
        self.assertFalse(d.eligible)
        self.assertEqual(d.reason, ShutdownReason.NO_WORKLOAD)

    def test_removed_tasks_are_not_finished_work(self):
        """Add-then-remove must not look like a completed workload."""
        d = self.decide(removed=3)
        self.assertFalse(d.eligible)
        self.assertEqual(d.reason, ShutdownReason.NO_WORKLOAD)

    def test_future_one_off_schedule_blocks(self):
        d = self.decide(completed=1, scheduled_at=self.now + 600)
        self.assertFalse(d.eligible)
        self.assertEqual(d.reason, ShutdownReason.SCHEDULED_TASK_PENDING)

    def test_past_one_off_schedule_does_not_block(self):
        d = self.decide(completed=1, scheduled_at=self.now - 1)
        self.assertTrue(d.eligible)

    def test_active_outranks_everything(self):
        d = self.decide(active=1, queued=1, paused=1, failed=1, cancelled=1)
        self.assertEqual(d.reason, ShutdownReason.ACTIVE_TASK)

    def test_queued_outranks_failed(self):
        d = self.decide(queued=1, failed=1)
        self.assertEqual(d.reason, ShutdownReason.QUEUED_TASK)

    def test_decision_is_serialisable(self):
        payload = self.decide(active=1).to_dict()
        self.assertEqual(payload["eligible"], False)
        self.assertEqual(payload["reason"], "active_task")
        self.assertEqual(payload["queue"]["active"], 1)


class QueueStateTest(unittest.TestCase):
    def test_counts_by_state(self):
        snaps = [
            SimpleNamespace(state=TaskStatus.DOWNLOADING),
            SimpleNamespace(state=TaskStatus.ANALYZING),
            SimpleNamespace(state=TaskStatus.MERGING),
            SimpleNamespace(state=TaskStatus.VERIFYING),
            SimpleNamespace(state=TaskStatus.STARTING),
            SimpleNamespace(state=TaskStatus.QUEUED),
            SimpleNamespace(state=TaskStatus.PAUSED),
            SimpleNamespace(state=TaskStatus.COMPLETED),
            SimpleNamespace(state=TaskStatus.FAILED),
            SimpleNamespace(state=TaskStatus.CANCELLED),
            SimpleNamespace(state=TaskStatus.REMOVED),
        ]
        q = QueueState.from_snapshots(snaps)
        self.assertEqual(q.active, 5)
        self.assertEqual(q.queued, 1)
        self.assertEqual(q.paused, 1)
        self.assertEqual(q.completed, 1)
        self.assertEqual(q.failed, 1)
        self.assertEqual(q.cancelled, 1)
        self.assertEqual(q.removed, 1)
        self.assertEqual(q.total, 11)
        self.assertEqual(q.blocking, 7)
        self.assertEqual(q.finished, 3)

    def test_merging_and_verifying_count_as_active(self):
        """A transfer that is merging segments is not finished."""
        q = QueueState.from_snapshots([SimpleNamespace(state=TaskStatus.MERGING)])
        self.assertFalse(q.is_idle)

    def test_string_states_are_accepted(self):
        q = QueueState.from_snapshots([SimpleNamespace(state="Complete")])
        self.assertEqual(q.completed, 1)

    def test_unknown_state_is_ignored_not_counted(self):
        q = QueueState.from_snapshots([SimpleNamespace(state="Nonsense")])
        self.assertEqual(q.total, 0)

    def test_snapshot_without_state_is_ignored(self):
        q = QueueState.from_snapshots([SimpleNamespace()])
        self.assertEqual(q.total, 0)


# ── Windows power abstraction ───────────────────────────────────────────────


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RecordingRunner:
    def __init__(self, results=None, exc=None):
        self.calls = []
        self.results = list(results or [])
        self.exc = exc

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        if self.exc is not None:
            raise self.exc
        return self.results.pop(0) if self.results else _Proc(0)


class WindowsPowerControllerTest(unittest.TestCase):
    def make(self, runner, *, platform="win32"):
        return WindowsPowerController(platform=platform, runner=runner)

    # -- capability --------------------------------------------------------

    def test_unsupported_off_windows(self):
        self.assertFalse(self.make(RecordingRunner(), platform="linux").is_supported())

    def test_supported_on_windows_when_exe_found(self):
        with patch("core.auto_shutdown.shutil.which", return_value="C:/x/shutdown.exe"):
            self.assertTrue(self.make(RecordingRunner()).is_supported())

    def test_missing_executable_is_unsupported(self):
        with patch("core.auto_shutdown.shutil.which", return_value=None):
            self.assertFalse(self.make(RecordingRunner()).is_supported())

    def test_kill_switch_disables_the_real_controller(self):
        """N13_NO_REAL_SHUTDOWN must make the real controller inert."""
        with patch.dict(os.environ, {"N13_NO_REAL_SHUTDOWN": "1"}):
            self.assertFalse(real_shutdown_allowed())
            self.assertFalse(self.make(RecordingRunner()).is_supported())

    def test_kill_switch_values(self):
        for value in ("1", "true", "YES", " on "):
            with patch.dict(os.environ, {"N13_NO_REAL_SHUTDOWN": value}):
                self.assertFalse(real_shutdown_allowed(), value)
        for value in ("", "0", "false", "no"):
            with patch.dict(os.environ, {"N13_NO_REAL_SHUTDOWN": value}):
                self.assertTrue(real_shutdown_allowed(), value)

    def test_operations_refuse_when_unsupported(self):
        ctl = self.make(RecordingRunner(), platform="linux")
        for call in (lambda: ctl.schedule_shutdown(60),
                     ctl.cancel_shutdown,
                     ctl.shutdown_now):
            with self.assertRaises(PowerUnsupportedError):
                call()

    # -- command construction ---------------------------------------------

    def test_schedule_uses_an_argument_array_and_no_shell(self):
        runner = RecordingRunner([_Proc(0)])
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            self.make(runner).schedule_shutdown(60)
        argv, kwargs = runner.calls[0]
        self.assertEqual(argv[0], "shutdown")
        self.assertEqual(argv[1:4], ["/s", "/t", "60"])
        self.assertIs(kwargs["shell"], False)
        self.assertIn("capture_output", kwargs)

    def test_comment_is_a_fixed_constant(self):
        """No task metadata may ever reach the command line."""
        runner = RecordingRunner([_Proc(0)])
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            self.make(runner).schedule_shutdown(60)
        argv = runner.calls[0][0]
        self.assertIn("/c", argv)
        self.assertIn("N13", argv[argv.index("/c") + 1])

    def test_cancel_uses_the_abort_switch(self):
        runner = RecordingRunner([_Proc(0)])
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            self.make(runner).cancel_shutdown()
        self.assertEqual(runner.calls[0][0][1], "/a")

    def test_shutdown_now_uses_zero_delay(self):
        runner = RecordingRunner([_Proc(0)])
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            self.make(runner).shutdown_now()
        self.assertEqual(runner.calls[0][0][1:4], ["/s", "/t", "0"])

    # -- delay validation --------------------------------------------------

    def test_invalid_delays_are_rejected_before_running(self):
        runner = RecordingRunner()
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            ctl = self.make(runner)
            for bad in ("abc", None, -1, 10 ** 12, 3.7j):
                with self.assertRaises(PowerError):
                    ctl.schedule_shutdown(bad)
        self.assertEqual(runner.calls, [])

    def test_float_delay_is_coerced(self):
        runner = RecordingRunner([_Proc(0)])
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            self.make(runner).schedule_shutdown(60.0)
        self.assertEqual(runner.calls[0][0][3], "60")

    # -- exit codes / failures --------------------------------------------

    def test_schedule_failure_raises(self):
        runner = RecordingRunner([_Proc(5, stderr="Access is denied.")])
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            with self.assertRaises(PowerError) as ctx:
                self.make(runner).schedule_shutdown(60)
        self.assertIn("Access is denied", str(ctx.exception))

    def test_cancel_failure_raises(self):
        runner = RecordingRunner([_Proc(5, stderr="Access is denied.")])
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            with self.assertRaises(PowerError):
                self.make(runner).cancel_shutdown()

    def test_cancel_is_idempotent_when_nothing_is_pending(self):
        """`shutdown /a` with nothing scheduled is success, not an error."""
        realistic = _Proc(
            1116,
            stderr="Unable to abort the system shutdown because no shutdown "
                   "was in progress.(1116)",
        )
        for proc in (_Proc(1116), realistic):
            runner = RecordingRunner([proc])
            with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
                self.make(runner).cancel_shutdown()   # must not raise

    def test_missing_binary_maps_to_unsupported(self):
        runner = RecordingRunner(exc=FileNotFoundError("shutdown"))
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            with self.assertRaises(PowerUnsupportedError):
                self.make(runner).schedule_shutdown(60)

    def test_timeout_maps_to_power_error(self):
        import subprocess as sp
        runner = RecordingRunner(exc=sp.TimeoutExpired("shutdown", 10))
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            with self.assertRaises(PowerError):
                self.make(runner).schedule_shutdown(60)

    def test_oserror_maps_to_power_error(self):
        runner = RecordingRunner(exc=OSError("boom"))
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            with self.assertRaises(PowerError):
                self.make(runner).schedule_shutdown(60)

    def test_output_is_sanitised(self):
        """Logged output must be single-line, NUL-free and bounded."""
        runner = RecordingRunner([_Proc(5, stderr="bad\x00thing\n" * 200)])
        with patch("core.auto_shutdown.shutil.which", return_value="shutdown"):
            with self.assertRaises(PowerError) as ctx:
                self.make(runner).schedule_shutdown(60)
        message = str(ctx.exception)
        self.assertNotIn("\x00", message)
        self.assertNotIn("\n", message)
        self.assertLess(len(message), 400)


class FakePowerControllerTest(unittest.TestCase):
    def test_records_calls_and_mirrors_pending(self):
        fake = FakePowerController()
        self.assertEqual(fake.schedule_count, 0)
        fake.schedule_shutdown(60)
        self.assertTrue(fake.pending)
        self.assertEqual(fake.scheduled_delays, [60])
        fake.cancel_shutdown()
        self.assertFalse(fake.pending)
        self.assertEqual(fake.cancel_count, 1)
        fake.shutdown_now()
        self.assertEqual(fake.now_count, 1)

    def test_failure_injection(self):
        fake = FakePowerController(fail_schedule=True)
        with self.assertRaises(PowerError):
            fake.schedule_shutdown(60)
        self.assertFalse(fake.pending)
        fake = FakePowerController(fail_cancel=True)
        with self.assertRaises(PowerError):
            fake.cancel_shutdown()

    def test_unsupported_fake(self):
        fake = FakePowerController(supported=False)
        self.assertFalse(fake.is_supported())
        with self.assertRaises(PowerUnsupportedError):
            fake.schedule_shutdown(60)


# ── Controller: basic lifecycle ─────────────────────────────────────────────


class ControllerLifecycleTest(unittest.TestCase):
    def test_preference_off_is_disabled(self):
        h = Harness(completed())
        h.controller.start()
        self.assertEqual(h.state, ShutdownState.DISABLED.value)
        self.assertEqual(h.power.schedule_count, 0)

    def test_arming_with_an_empty_queue_does_not_shut_down(self):
        h = Harness()
        h.arm()
        self.assertEqual(h.state, ShutdownState.ARMED.value)
        self.assertEqual(h.power.schedule_count, 0)
        self.assertFalse(h.controller.pending)
        self.assertEqual(h.status["reason"], "no_workload")

    def test_all_completed_schedules_exactly_one_shutdown(self):
        h = Harness(completed(3))
        h.arm()
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)
        self.assertEqual(h.power.schedule_count, 1)
        self.assertEqual(h.power.scheduled_delays, [DEFAULT_COUNTDOWN_SECONDS])
        self.assertTrue(h.controller.pending)

    def test_active_task_blocks(self):
        h = Harness([TaskStatus.DOWNLOADING])
        h.arm()
        self.assertEqual(h.state, ShutdownState.WAITING.value)
        self.assertEqual(h.status["reason"], "active_task")
        self.assertEqual(h.power.schedule_count, 0)

    def test_queued_task_blocks(self):
        h = Harness([TaskStatus.QUEUED])
        h.arm()
        self.assertEqual(h.state, ShutdownState.WAITING.value)
        self.assertEqual(h.status["reason"], "queued_task")

    def test_paused_task_blocks(self):
        h = Harness([TaskStatus.PAUSED])
        h.arm()
        self.assertEqual(h.state, ShutdownState.WAITING.value)
        self.assertEqual(h.status["reason"], "paused_task")
        self.assertEqual(h.power.schedule_count, 0)

    def test_failed_task_blocks_with_a_distinct_state(self):
        h = Harness([TaskStatus.COMPLETED, TaskStatus.FAILED])
        h.arm()
        self.assertEqual(h.state, ShutdownState.BLOCKED.value)
        self.assertEqual(h.status["reason"], "task_failed")

    def test_cancelled_task_blocks(self):
        h = Harness([TaskStatus.COMPLETED, TaskStatus.CANCELLED])
        h.arm()
        self.assertEqual(h.state, ShutdownState.BLOCKED.value)
        self.assertEqual(h.status["reason"], "task_cancelled")

    def test_failures_can_be_allowed_by_policy(self):
        cfg = AppConfig()
        cfg.shutdown_allow_failures = True
        h = Harness([TaskStatus.COMPLETED, TaskStatus.FAILED], cfg=cfg)
        h.arm()
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)

    def test_cancellations_can_be_allowed_by_policy(self):
        cfg = AppConfig()
        cfg.shutdown_allow_cancelled = True
        h = Harness([TaskStatus.COMPLETED, TaskStatus.CANCELLED], cfg=cfg)
        h.arm()
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)

    def test_countdown_seconds_comes_from_the_config(self):
        cfg = AppConfig()
        cfg.shutdown_countdown_seconds = 120
        h = Harness(completed(), cfg=cfg)
        h.arm()
        self.assertEqual(h.power.scheduled_delays, [120])
        self.assertEqual(h.status["countdown_seconds"], 120)

    def test_countdown_seconds_is_clamped(self):
        cfg = AppConfig()
        cfg.shutdown_countdown_seconds = 10 ** 9
        h = Harness(completed(), cfg=cfg)
        h.arm()
        self.assertLessEqual(h.power.scheduled_delays[0], 3600)
        cfg = AppConfig()
        cfg.shutdown_countdown_seconds = -5
        h = Harness(completed(), cfg=cfg)
        h.arm()
        self.assertGreaterEqual(h.power.scheduled_delays[0], 1)

    def test_a_non_numeric_countdown_falls_back_to_the_default(self):
        """A hand-edited config file must not crash or produce a 0s window."""
        cfg = AppConfig()
        cfg.shutdown_countdown_seconds = "junk"
        h = Harness(completed(), cfg=cfg)
        h.arm()
        self.assertEqual(h.power.scheduled_delays, [DEFAULT_COUNTDOWN_SECONDS])

    def test_unreadable_queue_fails_safe(self):
        """If the queue cannot be read we must assume work is still running."""
        h = Harness(completed())
        h.manager.raise_on_snapshots = True
        h.arm()
        self.assertEqual(h.power.schedule_count, 0)
        self.assertIn(h.state, (ShutdownState.WAITING.value, ShutdownState.BLOCKED.value))

    def test_unsupported_platform_reports_error_and_never_schedules(self):
        h = Harness(completed(), power=FakePowerController(supported=False))
        h.arm()
        self.assertEqual(h.state, ShutdownState.ERROR.value)
        self.assertEqual(h.status["reason"], "power_unsupported")
        self.assertEqual(h.power.schedule_count, 0)

    def test_preference_is_never_used_as_runtime_state(self):
        """enabled=True with live work must not read as "pending"."""
        h = Harness([TaskStatus.DOWNLOADING])
        h.arm()
        self.assertTrue(h.status["enabled"])
        self.assertFalse(h.status["pending"])
        self.assertEqual(h.state, ShutdownState.WAITING.value)

    def test_status_payload_has_the_documented_shape(self):
        h = Harness(completed())
        h.arm()
        payload = h.status
        for key in ("enabled", "state", "supported", "pending", "countdown_seconds",
                    "seconds_remaining", "deadline", "reason", "cancel_reason",
                    "blocked_reason", "last_error", "session"):
            self.assertIn(key, payload)
        for key in ("session_id", "armed_at", "countdown_started_at", "queue", "policy"):
            self.assertIn(key, payload["session"])

    def test_seconds_remaining_counts_down(self):
        h = Harness(completed())
        h.arm()
        h.clock.advance(10)
        self.assertAlmostEqual(h.status["seconds_remaining"], 50.0, places=3)

    def test_subscribe_and_unsubscribe(self):
        h = Harness(completed())
        seen = []
        unsub = h.controller.subscribe(seen.append)
        h.arm()
        self.assertTrue(seen)
        unsub()
        before = len(seen)
        h.controller.evaluate()
        self.assertEqual(len(seen), before)

    def test_a_raising_listener_does_not_break_the_controller(self):
        h = Harness(completed())
        h.controller.subscribe(lambda _p: (_ for _ in ()).throw(RuntimeError("x")))
        h.arm()
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)


# ── Countdown race conditions (Cases A–G) ───────────────────────────────────


class CountdownRaceTest(unittest.TestCase):
    """The scenario the old implementation got wrong."""

    def counting_down(self, **kw):
        h = Harness(completed(2), **kw)
        h.arm()
        assert h.state == ShutdownState.COUNTDOWN.value
        return h

    def test_case_b_new_download_cancels_the_pending_shutdown(self):
        h = self.counting_down()
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.QUEUED)
        h.notify("added")
        self.assertEqual(h.state, ShutdownState.CANCELLED.value)
        self.assertEqual(h.power.cancel_count, 1)
        self.assertFalse(h.controller.pending)
        self.assertEqual(h.status["cancel_reason"], "new_download")
        # The very next real event settles into the truthful waiting state.
        h.notify("started", SimpleNamespace(state=TaskStatus.DOWNLOADING))
        self.assertEqual(h.state, ShutdownState.WAITING.value)

    def test_case_c_resume_cancels_the_pending_shutdown(self):
        h = self.counting_down()
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.DOWNLOADING)
        h.notify("updated", SimpleNamespace(state=TaskStatus.DOWNLOADING))
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.status["cancel_reason"], "download_resumed")
        self.assertEqual(h.state, ShutdownState.CANCELLED.value)
        h.notify("updated", SimpleNamespace(state=TaskStatus.DOWNLOADING))
        self.assertEqual(h.state, ShutdownState.WAITING.value)

    def test_case_d_retry_pending_cancels_the_pending_shutdown(self):
        h = self.counting_down()
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.QUEUED)
        h.notify("updated", SimpleNamespace(state=TaskStatus.QUEUED))
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.status["cancel_reason"], "task_retrying")
        self.assertEqual(h.power.schedule_count, 1)

    def test_case_e_user_disables_cancels_immediately(self):
        h = self.counting_down()
        h.cfg.shutdown_when_done = False
        h.controller.set_enabled(False)
        self.assertEqual(h.state, ShutdownState.DISABLED.value)
        self.assertEqual(h.power.cancel_count, 1)
        self.assertFalse(h.controller.pending)
        self.assertFalse(h.status["enabled"])

    def test_case_f_removing_a_task_re_evaluates_rather_than_blindly_cancelling(self):
        """A removal that leaves completed work behind keeps the countdown."""
        h = self.counting_down()
        h.manager.set(TaskStatus.COMPLETED)
        h.notify("removed")
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)
        self.assertEqual(h.power.cancel_count, 0)

    def test_case_f_removing_the_last_task_cancels(self):
        h = self.counting_down()
        h.manager.set()
        h.notify("removed")
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.state, ShutdownState.CANCELLED.value)
        # Nothing to wait for any more; the next event reports ARMED.
        h.notify("queue_changed")
        self.assertEqual(h.state, ShutdownState.ARMED.value)

    def test_case_g_any_meaningful_event_re_evaluates(self):
        h = self.counting_down()
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.ANALYZING)
        h.notify("queue_changed")
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.status["cancel_reason"], "active_task")
        self.assertEqual(h.state, ShutdownState.CANCELLED.value)

    def test_progress_events_are_ignored(self):
        h = self.counting_down()
        for _ in range(50):
            h.notify("progress", SimpleNamespace(state=TaskStatus.DOWNLOADING))
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)
        self.assertEqual(h.power.cancel_count, 0)

    def test_started_event_cancels(self):
        h = self.counting_down()
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.DOWNLOADING)
        h.notify("started", SimpleNamespace(state=TaskStatus.DOWNLOADING))
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.status["cancel_reason"], "task_started")

    def test_pause_during_countdown_cancels(self):
        h = self.counting_down()
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.PAUSED)
        h.notify("updated", SimpleNamespace(state=TaskStatus.PAUSED))
        self.assertEqual(h.power.cancel_count, 1)

    def test_paused_then_resumed_then_completed_arms_again(self):
        """The PAUSED → RESUMED → COMPLETED path must not allow an early exit."""
        h = Harness([TaskStatus.COMPLETED, TaskStatus.PAUSED])
        h.arm()
        self.assertEqual(h.power.schedule_count, 0)
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.DOWNLOADING)
        h.notify("updated", SimpleNamespace(state=TaskStatus.DOWNLOADING))
        self.assertEqual(h.power.schedule_count, 0)
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.COMPLETED)
        h.notify("finished")
        self.assertEqual(h.power.schedule_count, 1)
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)


# ── Concurrency / duplicate events ──────────────────────────────────────────


class ConcurrencyTest(unittest.TestCase):
    def test_simultaneous_completions_schedule_exactly_one_shutdown(self):
        h = Harness([TaskStatus.DOWNLOADING] * 3)
        h.arm()
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.DOWNLOADING, TaskStatus.DOWNLOADING)
        h.notify("finished")
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.DOWNLOADING)
        h.notify("finished")
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.COMPLETED)
        h.notify("finished")
        self.assertEqual(h.power.schedule_count, 1)
        self.assertEqual(h.timers.live_count, 1)

    def test_duplicate_events_do_not_schedule_twice(self):
        h = Harness(completed())
        h.arm()
        for _ in range(25):
            h.notify("finished")
        self.assertEqual(h.power.schedule_count, 1)
        self.assertEqual(h.timers.live_count, 1)

    def test_parallel_notify_from_many_threads_schedules_once(self):
        import threading

        h = Harness(completed(4))
        h.arm()
        barrier = threading.Barrier(8)
        errors = []

        def worker():
            try:
                barrier.wait(timeout=5)
                for _ in range(20):
                    h.notify("finished")
            except Exception as exc:      # pragma: no cover - diagnostic
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(h.power.schedule_count, 1)
        self.assertEqual(h.timers.live_count, 1)

    def test_there_is_never_more_than_one_countdown_timer(self):
        h = Harness(completed())
        h.arm()
        for _ in range(10):
            h.notify("queue_changed")
        self.assertLessEqual(h.timers.live_count, 1)


# ── Final safety check ──────────────────────────────────────────────────────


class FinalSafetyCheckTest(unittest.TestCase):
    def test_final_check_passes_and_consumes_the_preference(self):
        h = Harness(completed())
        h.arm()
        self.assertEqual(h.timers.live_count, 1)
        h.timers.fire()
        self.assertEqual(h.state, ShutdownState.EXECUTED.value)
        self.assertFalse(h.cfg.shutdown_when_done)
        self.assertFalse(h.controller.pending)
        self.assertGreaterEqual(h.persisted, 1)

    def test_final_check_interval_leaves_room_to_abort(self):
        h = Harness(completed())
        h.arm()
        self.assertEqual(h.timers.pending[0]["interval"], DEFAULT_COUNTDOWN_SECONDS - 5.0)

    def test_final_check_aborts_when_a_task_appeared(self):
        h = Harness(completed())
        h.arm()
        # A task appeared without any event reaching the controller (e.g. a
        # scheduler-driven start).  The last line of defence must catch it.
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.DOWNLOADING)
        h.timers.fire()
        self.assertEqual(h.state, ShutdownState.WAITING.value)
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.status["cancel_reason"], "final_check_failed")
        self.assertTrue(h.controller.final_check_failed)

    def test_final_check_aborts_on_failures_that_appeared(self):
        h = Harness(completed())
        h.arm()
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.FAILED)
        h.timers.fire()
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.state, ShutdownState.BLOCKED.value)

    def test_final_check_is_a_no_op_after_a_cancel(self):
        h = Harness(completed())
        h.arm()
        h.manager.set(TaskStatus.QUEUED)
        h.notify("added")
        self.assertEqual(h.power.cancel_count, 1)
        h.timers.fire()          # nothing should be live any more
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.power.schedule_count, 1)

    def test_unarmable_final_check_aborts_the_shutdown(self):
        """Without the pre-deadline re-validation the shutdown is unsafe."""
        h = Harness(completed())

        def broken_timer(_interval, _callback):
            raise RuntimeError("no timers here")

        h.controller._timer_factory = broken_timer
        h.arm()
        self.assertEqual(h.power.schedule_count, 1)
        self.assertEqual(h.power.cancel_count, 1)
        self.assertFalse(h.controller.pending)

    def test_grace_is_clamped_so_a_short_countdown_still_validates(self):
        h = Harness(completed(), countdown_grace=600.0)
        h.arm()
        self.assertGreater(h.timers.pending[0]["interval"], 0.0)


# ── Failure handling ────────────────────────────────────────────────────────


class FailureHandlingTest(unittest.TestCase):
    def test_schedule_failure_never_produces_countdown(self):
        power = FakePowerController(fail_schedule=True)
        h = Harness(completed(), power=power)
        h.arm()
        self.assertEqual(h.state, ShutdownState.ERROR.value)
        self.assertFalse(h.controller.pending)
        self.assertEqual(h.status["reason"], "schedule_failed")
        self.assertEqual(h.timers.live_count, 0)

    def test_schedule_failure_does_not_hammer_windows(self):
        power = FakePowerController(fail_schedule=True)
        h = Harness(completed(), power=power)
        h.arm()
        for _ in range(20):
            h.notify("queue_changed")
        self.assertEqual(power.schedule_attempts, 1)
        self.assertEqual(power.schedule_count, 0)

    def test_schedule_retries_after_the_cooldown(self):
        power = FakePowerController(fail_schedule=True)
        h = Harness(completed(), power=power)
        h.arm()
        h.clock.advance(h.controller._RESCHEDULE_COOLDOWN + 1)
        h.notify("queue_changed")
        self.assertEqual(power.schedule_attempts, 2)

    def test_a_new_workload_resets_the_failure_latch(self):
        power = FakePowerController(fail_schedule=True)
        h = Harness(completed(), power=power)
        h.arm()
        h.manager.set()
        h.notify("removed")            # NO_WORKLOAD clears the latch
        self.assertEqual(h.state, ShutdownState.ARMED.value)
        power.fail_schedule = False
        h.manager.set(TaskStatus.COMPLETED)
        h.notify("finished")
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)

    def test_cancel_failure_is_reported_honestly(self):
        h = Harness(completed())
        h.arm()
        h.power.fail_cancel = True
        h.manager.set(TaskStatus.QUEUED)
        h.notify("added")
        self.assertEqual(h.state, ShutdownState.ERROR.value)
        self.assertTrue(h.controller.pending)
        self.assertEqual(h.status["cancel_reason"], "cancel_failed")
        self.assertIn("injected failure", h.status["last_error"])

    def test_cancel_failure_is_retried_on_the_next_evaluation(self):
        h = Harness(completed())
        h.arm()
        h.power.fail_cancel = True
        h.manager.set(TaskStatus.QUEUED)
        h.notify("added")
        self.assertTrue(h.controller.pending)
        h.power.fail_cancel = False
        h.notify("queue_changed")
        self.assertFalse(h.controller.pending)
        self.assertEqual(h.state, ShutdownState.CANCELLED.value)
        self.assertEqual(h.status["last_error"], "")

    def test_failure_state_is_not_overwritten_while_the_os_is_pending(self):
        """A rosier state must never hide an un-cancellable shutdown."""
        h = Harness(completed())
        h.arm()
        h.power.fail_cancel = True
        h.manager.set()
        h.notify("removed")
        self.assertEqual(h.state, ShutdownState.ERROR.value)
        self.assertTrue(h.controller.pending)

    def test_scheduler_read_failure_is_treated_as_gating(self):
        def broken():
            raise RuntimeError("scheduler down")

        h = Harness([TaskStatus.QUEUED], scheduler=broken)
        h.arm()
        self.assertEqual(h.power.schedule_count, 0)
        self.assertEqual(h.state, ShutdownState.WAITING.value)


# ── Recovery / application close ────────────────────────────────────────────


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_file = self.tmp / "auto_shutdown.json"
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            if self.state_file.exists():
                self.state_file.unlink()
        except OSError:
            pass

    def test_marker_is_written_on_countdown_and_cleared_on_cancel(self):
        h = Harness(completed(), state_file=self.state_file)
        h.arm()
        self.assertTrue(self.state_file.exists())
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(data["state"], "Countdown")
        self.assertEqual(data["delay_seconds"], DEFAULT_COUNTDOWN_SECONDS)
        h.manager.set(TaskStatus.QUEUED)
        h.notify("added")
        self.assertFalse(self.state_file.exists())

    def test_marker_is_cleared_after_a_successful_shutdown(self):
        h = Harness(completed(), state_file=self.state_file)
        h.arm()
        h.timers.fire()
        self.assertFalse(self.state_file.exists())

    def test_stale_marker_is_cancelled_on_the_next_start(self):
        self.state_file.write_text(
            json.dumps({"version": 1, "state": "Countdown", "delay_seconds": 60}),
            encoding="utf-8",
        )
        h = Harness(state_file=self.state_file)
        reason = h.controller.recover_stale_shutdown()
        self.assertEqual(reason, ShutdownReason.STALE_SESSION)
        self.assertEqual(h.power.cancel_count, 1)
        self.assertFalse(self.state_file.exists())

    def test_recovery_is_a_no_op_without_a_marker(self):
        h = Harness(state_file=self.state_file)
        self.assertIsNone(h.controller.recover_stale_shutdown())
        self.assertEqual(h.power.cancel_count, 0)

    def test_recovery_keeps_the_marker_when_cancellation_fails(self):
        self.state_file.write_text(
            json.dumps({"state": "Countdown"}), encoding="utf-8"
        )
        h = Harness(state_file=self.state_file)
        h.power.fail_cancel = True
        self.assertEqual(
            h.controller.recover_stale_shutdown(), ShutdownReason.SYSTEM_ERROR
        )
        self.assertTrue(self.state_file.exists())

    def test_corrupt_marker_is_ignored(self):
        self.state_file.write_text("{not json", encoding="utf-8")
        h = Harness(state_file=self.state_file)
        self.assertIsNone(h.controller.recover_stale_shutdown())

    def test_marker_for_a_non_countdown_state_is_discarded(self):
        self.state_file.write_text(
            json.dumps({"state": "Armed"}), encoding="utf-8"
        )
        h = Harness(state_file=self.state_file)
        self.assertIsNone(h.controller.recover_stale_shutdown())
        self.assertEqual(h.power.cancel_count, 0)
        self.assertFalse(self.state_file.exists())

    def test_unsupported_platform_clears_the_marker_without_retrying(self):
        self.state_file.write_text(
            json.dumps({"state": "Countdown"}), encoding="utf-8"
        )
        h = Harness(state_file=self.state_file, power=FakePowerController(supported=False))
        self.assertIsNone(h.controller.recover_stale_shutdown())
        self.assertFalse(self.state_file.exists())

    def test_application_close_cancels_a_pending_shutdown(self):
        """N13 exiting can no longer re-validate, so it must not leave one armed."""
        h = Harness(completed())
        h.arm()
        h.controller.shutdown()
        self.assertEqual(h.power.cancel_count, 1)
        self.assertFalse(h.controller.pending)
        self.assertEqual(h.state, ShutdownState.DISABLED.value)

    def test_a_crash_mid_countdown_is_cancelled_then_re_armed(self):
        """Intentional, documented behaviour.

        The preference is one-shot and is consumed by *firing*, not by a crash,
        so after clearing the stale OS shutdown a still-eligible queue re-arms.
        The user asked for the machine to power off once the work was done, and
        the new countdown is visible and cancellable.
        """
        self.state_file.write_text(
            json.dumps({"state": "Countdown", "delay_seconds": 60}), encoding="utf-8"
        )
        h = Harness(completed(), state_file=self.state_file)
        h.cfg.shutdown_when_done = True
        h.controller.start()
        self.assertEqual(h.power.cancel_count, 1)      # the stale one is aborted
        self.assertEqual(h.power.schedule_count, 1)    # then a fresh one is armed
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)
        # A new countdown is live, so the marker is rewritten for the next crash.
        self.assertTrue(self.state_file.exists())
        self.assertEqual(
            json.loads(self.state_file.read_text(encoding="utf-8"))["state"], "Countdown"
        )

    def test_a_crash_mid_countdown_does_not_re_arm_when_work_remains(self):
        self.state_file.write_text(
            json.dumps({"state": "Countdown", "delay_seconds": 60}), encoding="utf-8"
        )
        h = Harness([TaskStatus.COMPLETED, TaskStatus.QUEUED], state_file=self.state_file)
        h.cfg.shutdown_when_done = True
        h.controller.start()
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.power.schedule_count, 0)
        self.assertEqual(h.state, ShutdownState.WAITING.value)

    def test_application_close_is_idempotent(self):
        h = Harness(completed())
        h.arm()
        h.controller.shutdown()
        h.controller.shutdown()
        self.assertEqual(h.power.cancel_count, 1)

    def test_notify_after_close_is_ignored(self):
        h = Harness(completed())
        h.arm()
        h.controller.shutdown()
        h.manager.set(TaskStatus.COMPLETED)
        h.notify("finished")
        self.assertEqual(h.power.schedule_count, 1)


# ── Scheduler interaction ───────────────────────────────────────────────────


class SchedulerTest(unittest.TestCase):
    def test_gated_queued_task_reports_the_scheduler_reason(self):
        snap = SchedulerSnapshot(enabled=True, gated=True)
        h = Harness([TaskStatus.QUEUED], scheduler=lambda: snap)
        h.arm()
        self.assertEqual(h.state, ShutdownState.WAITING.value)
        self.assertEqual(h.status["reason"], "scheduled_task_pending")
        self.assertEqual(h.power.schedule_count, 0)

    def test_gate_off_reports_the_generic_reason(self):
        snap = SchedulerSnapshot(enabled=True, gated=False)
        h = Harness([TaskStatus.QUEUED], scheduler=lambda: snap)
        h.arm()
        self.assertEqual(h.status["reason"], "queued_task")

    def test_scheduler_gate_closing_during_a_countdown_cancels(self):
        h = Harness(completed())
        h.arm()
        state = {"snap": SchedulerSnapshot()}
        h.controller._scheduler = lambda: state["snap"]
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.QUEUED)
        state["snap"] = SchedulerSnapshot(enabled=True, gated=True)
        h.notify("scheduler_changed")
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.status["cancel_reason"], "scheduled_task_pending")

    def test_future_one_off_schedule_blocks_shutdown(self):
        class Cfg(AppConfig):
            def get_schedule_datetime(self_inner):
                from datetime import datetime, timedelta
                return datetime.now() + timedelta(minutes=20)

        h = Harness(completed(), cfg=Cfg())
        h.arm()
        self.assertEqual(h.power.schedule_count, 0)
        self.assertEqual(h.status["reason"], "scheduled_task_pending")

    def test_past_one_off_schedule_does_not_block(self):
        class Cfg(AppConfig):
            def get_schedule_datetime(self_inner):
                from datetime import datetime, timedelta
                return datetime.now() - timedelta(minutes=20)

        h = Harness(completed(), cfg=Cfg())
        h.arm()
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)

    def test_scheduler_snapshot_also_carries_a_scheduled_time(self):
        from datetime import datetime, timedelta

        target = datetime.now() + timedelta(minutes=5)
        snap = SchedulerSnapshot(enabled=True, gated=False, scheduled_at=target.timestamp())
        h = Harness(completed(), scheduler=lambda: snap)
        h.arm()
        self.assertEqual(h.power.schedule_count, 0)


# ── User-initiated cancellation / loops ─────────────────────────────────────


class UserCancellationTest(unittest.TestCase):
    def test_user_cancel_stops_the_shutdown_and_disarms(self):
        h = Harness(completed())
        h.arm()
        h.controller.cancel()
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.state, ShutdownState.DISABLED.value)
        self.assertFalse(h.cfg.shutdown_when_done)
        self.assertFalse(h.status["enabled"])

    def test_user_cancel_does_not_immediately_re_arm(self):
        """The classic cancel → re-evaluate → schedule loop must be impossible."""
        h = Harness(completed())
        h.arm()
        h.controller.cancel()
        for _ in range(5):
            h.notify("queue_changed")
        self.assertEqual(h.power.schedule_count, 1)
        self.assertEqual(h.power.cancel_count, 1)
        self.assertEqual(h.state, ShutdownState.DISABLED.value)

    def test_cancel_with_nothing_pending_is_safe(self):
        h = Harness(completed())
        h.arm()
        h.manager.set(TaskStatus.QUEUED)
        h.notify("added")
        self.assertEqual(h.power.cancel_count, 1)
        h.controller.cancel()
        self.assertEqual(h.power.cancel_count, 1)

    def test_disable_then_reenable_works(self):
        h = Harness(completed())
        h.arm()
        h.controller.set_enabled(False)
        self.assertEqual(h.state, ShutdownState.DISABLED.value)
        h.controller.set_enabled(True)
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)
        self.assertEqual(h.power.schedule_count, 2)


# ── Safety invariants ───────────────────────────────────────────────────────


class InvariantTest(unittest.TestCase):
    """The properties that must hold no matter what.

    These matter more than line coverage: they are the formal statement of
    "never shut down while work is outstanding".
    """

    BLOCKING = [
        TaskStatus.QUEUED,
        TaskStatus.ANALYZING,
        TaskStatus.STARTING,
        TaskStatus.DOWNLOADING,
        TaskStatus.PAUSED,
        TaskStatus.MERGING,
        TaskStatus.VERIFYING,
    ]

    def test_invariant_no_blocking_task_ever_allows_a_shutdown(self):
        for status in self.BLOCKING:
            for extra in ([], [TaskStatus.COMPLETED], [TaskStatus.COMPLETED] * 5):
                h = Harness(list(extra) + [status])
                h.arm()
                self.assertEqual(
                    h.power.schedule_count, 0,
                    "{} must block shutdown (extra={})".format(status, extra),
                )
                self.assertNotEqual(h.state, ShutdownState.COUNTDOWN.value)

    def test_invariant_state_is_never_derived_from_the_preference_flag(self):
        """enabled=True is not enough to claim a shutdown is pending."""
        h = Harness([TaskStatus.DOWNLOADING])
        h.arm()
        self.assertTrue(h.cfg.shutdown_when_done)
        self.assertFalse(h.controller.pending)
        self.assertNotEqual(h.state, ShutdownState.COUNTDOWN.value)
        # And the converse: a live countdown with the preference already off.
        h.manager.set(TaskStatus.COMPLETED)
        h.notify("finished")
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)

    def test_invariant_a_failed_schedule_never_yields_countdown(self):
        for fail in (True, False):
            power = FakePowerController(fail_schedule=fail)
            h = Harness(completed(), power=power)
            h.arm()
            if fail:
                self.assertNotEqual(h.state, ShutdownState.COUNTDOWN.value)
                self.assertFalse(h.controller.pending)
            else:
                self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)

    def test_invariant_at_most_one_countdown_under_event_storms(self):
        h = Harness(completed(3))
        h.arm()
        events = ["finished", "updated", "queue_changed", "removed", "added",
                  "scheduler_changed", "config_changed"]
        for i in range(200):
            h.notify(events[i % len(events)], SimpleNamespace(state=TaskStatus.COMPLETED))
        self.assertLessEqual(h.power.schedule_count, 1)
        self.assertLessEqual(h.timers.live_count, 1)

    def test_invariant_a_blocking_task_invalidates_a_live_countdown(self):
        h = Harness(completed())
        h.arm()
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.QUEUED)
        h.notify("added")
        self.assertNotEqual(h.state, ShutdownState.COUNTDOWN.value)
        self.assertFalse(h.controller.pending)
        self.assertGreaterEqual(h.power.cancel_count, 1)

    def test_invariant_unknown_events_are_safe(self):
        h = Harness(completed())
        h.arm()
        for name in ("", "nonsense", "task_created", "task_removed", "config_changed"):
            h.notify(name, SimpleNamespace(state=TaskStatus.COMPLETED))
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)
        self.assertEqual(h.power.schedule_count, 1)

    def test_invariant_controller_never_crashes_on_a_broken_snapshot(self):
        h = Harness(completed())
        h.arm()
        h.notify("updated", object())        # no .state attribute at all
        self.assertEqual(h.power.schedule_count, 1)


# ── Adversarial review ──────────────────────────────────────────────────────


class AdversarialTest(unittest.TestCase):
    """Deliberate attempts to break the subsystem.

    Every case here is a scenario where a plausible implementation would
    either shut the machine down when it should not, or claim a state it is not
    actually in.
    """

    def test_out_of_order_events_do_not_break_the_state_machine(self):
        """``finished`` can overtake ``started`` for the same task.

        ``TaskManager`` starts the worker thread inside its lock but emits the
        ``started`` event after releasing it, so a very fast download can
        deliver ``finished`` first.  Neither order may produce a wrong verdict.
        """
        h = Harness([TaskStatus.COMPLETED])
        h.arm()
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)
        # The "started" event arrives late, after the task is already done.
        h.notify("started", SimpleNamespace(state=TaskStatus.COMPLETED))
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)
        self.assertEqual(h.power.schedule_count, 1)
        self.assertEqual(h.power.cancel_count, 0)

    def test_re_enabling_while_counting_down_does_not_schedule_twice(self):
        h = Harness(completed())
        h.arm()
        for _ in range(5):
            h.controller.set_enabled(True)
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)
        self.assertEqual(h.power.schedule_count, 1)

    def test_rapid_toggle_leaves_no_stray_pending_shutdown(self):
        h = Harness(completed())
        for _ in range(10):
            h.controller.set_enabled(True)
            h.controller.set_enabled(False)
        self.assertFalse(h.controller.pending)
        self.assertFalse(h.status["enabled"])
        self.assertEqual(h.power.schedule_count, h.power.cancel_count)
        self.assertEqual(h.state, ShutdownState.DISABLED.value)

    def test_disabling_via_config_and_a_config_event_cancels(self):
        """A preference flipped behind the controller's back is still honoured."""
        h = Harness(completed())
        h.arm()
        h.cfg.shutdown_when_done = False
        h.notify("config_changed")
        self.assertEqual(h.power.cancel_count, 1)
        self.assertFalse(h.controller.pending)
        self.assertEqual(h.state, ShutdownState.DISABLED.value)

    def test_user_cancel_that_fails_reports_error_not_success(self):
        h = Harness(completed())
        h.arm()
        h.power.fail_cancel = True
        status = h.controller.cancel()
        self.assertEqual(status["state"], ShutdownState.ERROR.value)
        self.assertTrue(status["pending"])
        self.assertEqual(status["cancel_reason"], "cancel_failed")

    def test_disable_that_fails_to_cancel_reports_error(self):
        h = Harness(completed())
        h.arm()
        h.power.fail_cancel = True
        status = h.controller.set_enabled(False)
        self.assertEqual(status["state"], ShutdownState.ERROR.value)
        self.assertTrue(status["pending"])
        self.assertFalse(status["enabled"])

    def test_cancelled_then_completed_re_arms(self):
        h = Harness(completed())
        h.arm()
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.QUEUED)
        h.notify("added")
        self.assertEqual(h.state, ShutdownState.CANCELLED.value)
        h.manager.set(TaskStatus.COMPLETED, TaskStatus.COMPLETED)
        h.notify("finished")
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)
        self.assertEqual(h.power.schedule_count, 2)
        self.assertEqual(h.power.cancel_count, 1)

    def test_a_cancel_is_never_followed_by_an_immediate_re_schedule(self):
        """The cancel/schedule oscillation must be impossible."""
        h = Harness(completed())
        h.arm()
        h.manager.set(TaskStatus.QUEUED)
        for i in range(50):
            h.notify("added" if i % 2 else "queue_changed")
        self.assertEqual(h.power.schedule_count, 1)
        self.assertEqual(h.power.cancel_count, 1)

    def test_unrelated_update_does_not_invalidate_a_countdown(self):
        """A speed-limit edit is not a reason to abort a shutdown."""
        h = Harness(completed())
        h.arm()
        h.notify("updated", SimpleNamespace(state=TaskStatus.COMPLETED))
        self.assertEqual(h.state, ShutdownState.COUNTDOWN.value)
        self.assertEqual(h.power.cancel_count, 0)

    def test_removed_task_is_never_counted_as_finished(self):
        """Add → remove must not look like a completed workload."""
        h = Harness()
        h.arm()
        h.manager.set(TaskStatus.REMOVED)
        h.notify("removed")
        self.assertEqual(h.power.schedule_count, 0)
        self.assertEqual(h.state, ShutdownState.ARMED.value)

    def test_final_check_timer_cannot_fire_twice(self):
        h = Harness(completed())
        h.arm()
        self.assertEqual(h.timers.live_count, 1)
        h.timers.fire()
        self.assertEqual(h.timers.live_count, 0)
        self.assertEqual(h.state, ShutdownState.EXECUTED.value)
        self.assertFalse(h.timers.fire())      # nothing left to fire

    def test_stale_final_check_after_a_cancel_is_a_no_op(self):
        """A timer that already fired must not resurrect a cancelled shutdown."""
        h = Harness(completed())
        h.arm()
        stale = h.timers.pending[0]["callback"]
        h.manager.set(TaskStatus.QUEUED)
        h.notify("added")
        self.assertEqual(h.state, ShutdownState.CANCELLED.value)
        stale()                                 # simulate the timer racing the cancel
        self.assertEqual(h.state, ShutdownState.CANCELLED.value)
        self.assertEqual(h.power.schedule_count, 1)

    def test_countdown_seconds_of_one_still_gets_a_final_check(self):
        cfg = AppConfig()
        cfg.shutdown_countdown_seconds = 1
        h = Harness(completed(), cfg=cfg)
        h.arm()
        self.assertGreater(h.timers.pending[0]["interval"], 0.0)
        self.assertEqual(h.power.scheduled_delays, [1])

    def test_unreadable_queue_aborts_a_live_countdown(self):
        """When in doubt the machine stays on — even mid-countdown."""
        h = Harness(completed())
        h.arm()
        h.power.calls.clear()
        h.manager.raise_on_snapshots = True
        h.notify("finished")
        self.assertEqual(h.power.schedule_attempts, 0)
        self.assertEqual(h.power.cancel_count, 1)
        self.assertFalse(h.controller.pending)

    def test_a_burst_of_new_tasks_only_cancels_once(self):
        h = Harness(completed())
        h.arm()
        h.manager.set(TaskStatus.QUEUED)
        for _ in range(30):
            h.notify("added")
        self.assertEqual(h.power.cancel_count, 1)


# ── CLI path ────────────────────────────────────────────────────────────────


class CliShutdownTest(unittest.TestCase):
    """The interactive console menu must use the same abstraction as the GUI.

    Before this was routed through ``WindowsPowerController`` the CLI shelled
    out on its own — a second, unvalidated path to the OS power state.
    """

    def _menu(self):
        try:
            from ui import menu
        except Exception as exc:      # pragma: no cover - optional CLI deps
            self.skipTest("ui.menu is not importable: {}".format(exc))
        return menu

    def test_windows_path_goes_through_the_power_controller(self):
        menu = self._menu()
        calls = []

        class Spy(WindowsPowerController):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)

            def is_supported(self):
                return True

            def schedule_shutdown(self, delay_seconds):
                calls.append(delay_seconds)

        with patch.object(menu.platform, "system", return_value="Windows"), \
             patch("core.auto_shutdown.WindowsPowerController", Spy), \
             patch("rich.prompt.Confirm.ask", return_value=True):
            self.assertTrue(menu.shutdown_computer(30))
        self.assertEqual(calls, [30])

    def test_declining_the_prompt_does_not_shut_down(self):
        menu = self._menu()
        calls = []
        with patch.object(menu.platform, "system", return_value="Windows"), \
             patch("core.auto_shutdown.WindowsPowerController") as spy, \
             patch("rich.prompt.Confirm.ask", return_value=False):
            self.assertFalse(menu.shutdown_computer(30))
        spy.assert_not_called()
        self.assertEqual(calls, [])

    def test_a_power_failure_is_reported_not_raised(self):
        menu = self._menu()
        with patch.object(menu.platform, "system", return_value="Windows"), \
             patch.object(WindowsPowerController, "is_supported", return_value=True), \
             patch.object(WindowsPowerController, "schedule_shutdown",
                          side_effect=PowerError("refused")), \
             patch("rich.prompt.Confirm.ask", return_value=True):
            self.assertFalse(menu.shutdown_computer(30))   # must not raise

    def test_unsupported_platform_is_reported(self):
        menu = self._menu()
        with patch.object(menu.platform, "system", return_value="Plan9"), \
             patch("rich.prompt.Confirm.ask", return_value=True):
            self.assertFalse(menu.shutdown_computer(30))

    def test_the_kill_switch_makes_the_cli_inert(self):
        """N13_NO_REAL_SHUTDOWN must neutralise the CLI too, not just the GUI."""
        menu = self._menu()
        with patch.object(menu.platform, "system", return_value="Windows"), \
             patch.dict(os.environ, {"N13_NO_REAL_SHUTDOWN": "1"}), \
             patch("rich.prompt.Confirm.ask", return_value=True):
            self.assertFalse(menu.shutdown_computer(30))

    def test_the_cli_never_shells_out_on_windows(self):
        """Regression guard: no ``subprocess`` call on the Windows branch."""
        menu = self._menu()
        with patch.object(menu.platform, "system", return_value="Windows"), \
             patch.object(WindowsPowerController, "is_supported", return_value=True), \
             patch.object(WindowsPowerController, "schedule_shutdown") as sched, \
             patch.object(menu.subprocess, "run") as run, \
             patch("rich.prompt.Confirm.ask", return_value=True):
            menu.shutdown_computer(30)
        sched.assert_called_once_with(30)
        run.assert_not_called()


# ── Real TaskManager integration ────────────────────────────────────────────


SIZE = 1000


class FastRunner:
    """Deterministic runner: no network, finishes almost immediately."""

    def __init__(self):
        self.last_error = ""

    def analyze(self, task_id, request, control):
        return SimpleNamespace(ok=True, total_size=SIZE, supports_range=True,
                               filename="file.zip", content_type="application/zip",
                               server="test", etag="", last_modified="")

    def download(self, task_id, request, analysis, progress, control,
                 status_callback=None, path_callback=None, smart_callback=None):
        if status_callback:
            status_callback("DOWNLOADING")
        progress(SIZE, SIZE)
        return True


def wait_until(cond, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


class EndToEndTest(unittest.TestCase):
    """TaskManager → controller → power, with the real event plumbing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AppConfig()
        self.cfg.download_dir = str(self.tmp / "downloads")
        self.manager = TaskManager(
            FastRunner(), self.tmp / "data", max_concurrent=2, config=self.cfg
        )
        self.power = FakePowerController()
        self.timers = ManualTimerFactory()
        self.controller = AutoShutdownController(
            self.manager, self.cfg, power=self.power, timer_factory=self.timers,
            persist=lambda: None, clock=time.time,
        )
        # The same wiring ``Api`` uses.
        self.manager.subscribe(lambda ev, snap: self.controller.notify(ev, snap))

    def tearDown(self):
        try:
            self.manager.close()
        except Exception:
            pass

    def add(self, name, autostart=True):
        return self.manager.add(DownloadRequest(
            url="https://a.example/" + name, directory=str(self.tmp / "downloads"),
            checksum=None, label=name, category="Other", priority=5,
            speed_limit_bps=0, connection_mode="manual", num_threads=1,
            probe_analysis=None,
        ), autostart=autostart)

    def test_real_queue_drain_schedules_exactly_one_shutdown(self):
        self.cfg.shutdown_when_done = True
        self.add("a.zip")
        self.add("b.zip")
        self.assertTrue(wait_until(lambda: self.controller.state
                                   == ShutdownState.COUNTDOWN))
        self.assertEqual(self.power.schedule_count, 1)
        self.assertEqual(self.power.scheduled_delays, [DEFAULT_COUNTDOWN_SECONDS])
        self.assertTrue(self.controller.pending)

    def test_real_queue_with_a_queued_task_does_not_schedule(self):
        self.cfg.shutdown_when_done = True
        self.add("a.zip", autostart=False)
        time.sleep(0.3)
        self.assertEqual(self.power.schedule_count, 0)
        self.assertEqual(self.controller.state, ShutdownState.WAITING)

    def test_adding_a_real_task_during_the_countdown_cancels_it(self):
        self.cfg.shutdown_when_done = True
        self.add("a.zip")
        self.assertTrue(wait_until(lambda: self.controller.state
                                   == ShutdownState.COUNTDOWN))
        self.add("late.zip")
        self.assertTrue(wait_until(lambda: self.power.cancel_count >= 1))
        self.assertFalse(self.controller.pending)

    def test_removing_a_real_task_from_a_live_countdown_keeps_it_armed(self):
        self.cfg.shutdown_when_done = True
        self.add("a.zip")
        self.add("b.zip")
        self.assertTrue(wait_until(lambda: self.controller.state
                                   == ShutdownState.COUNTDOWN))
        ids = [s.id for s in self.manager.snapshots()]
        self.manager.remove_task(ids[0])
        time.sleep(0.3)
        self.assertEqual(self.controller.state, ShutdownState.COUNTDOWN)
        self.assertEqual(self.power.cancel_count, 0)

    def test_failed_real_download_blocks_by_default(self):
        class FailingRunner(FastRunner):
            def download(self, task_id, request, analysis, progress, control,
                         status_callback=None, path_callback=None, smart_callback=None):
                self.last_error = "boom"
                return False

        self.manager.close()
        self.manager = TaskManager(
            FailingRunner(), self.tmp / "data2", max_concurrent=1, config=self.cfg
        )
        self.controller._manager = self.manager
        self.manager.subscribe(lambda ev, snap: self.controller.notify(ev, snap))
        self.cfg.shutdown_when_done = True
        self.add("a.zip")
        self.assertTrue(wait_until(
            lambda: self.controller.state == ShutdownState.BLOCKED
        ))
        self.assertEqual(self.power.schedule_count, 0)
        self.assertEqual(self.controller.status()["reason"], "task_failed")


# ── API layer integration ───────────────────────────────────────────────────


class ApiIntegrationTest(unittest.TestCase):
    """The API surface the UI talks to, with the power layer faked out."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AppConfig()
        self.cfg.auto_start_server = False
        self.cfg.download_dir = str(self.tmp / "downloads")
        from unittest.mock import patch as _patch
        import core.paths
        self._patch = _patch.object(core.paths, "data_dir", return_value=self.tmp / "data")
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.power = FakePowerController()
        from ui.api import Api
        from core.session import SessionManager
        self.api = Api(self.cfg, SessionManager(self.cfg), auto_shutdown_power=self.power)
        self.addCleanup(self.api.shutdown)

    def test_status_shape(self):
        status = self.api.get_auto_shutdown_status()
        self.assertIn("state", status)
        self.assertIn("supported", status)
        self.assertFalse(status["enabled"])
        self.assertEqual(status["state"], ShutdownState.DISABLED.value)

    def test_set_auto_shutdown_arms_and_disarms(self):
        status = self.api.set_auto_shutdown(True)
        self.assertTrue(status["enabled"])
        self.assertEqual(status["state"], ShutdownState.ARMED.value)
        self.assertEqual(self.power.schedule_count, 0)
        status = self.api.set_auto_shutdown(False)
        self.assertFalse(status["enabled"])
        self.assertEqual(status["state"], ShutdownState.DISABLED.value)

    def test_status_reflects_real_queue_state(self):
        """A queued task must read as "Waiting", not as an armed-but-idle state."""
        self.api.set_auto_shutdown(True)
        self.api.add_download("https://a.example/x.zip", directory=str(self.tmp),
                              label="x.zip", autostart=False)
        status = self.api.get_auto_shutdown_status()
        self.assertEqual(status["state"], ShutdownState.WAITING.value)
        self.assertEqual(status["reason"], "queued_task")
        self.assertEqual(self.power.schedule_count, 0)

    def test_update_settings_arms_the_controller(self):
        self.api.update_settings({"shutdown_when_done": True})
        status = self.api.get_auto_shutdown_status()
        self.assertTrue(status["enabled"])
        self.assertTrue(self.cfg.shutdown_when_done)

    def test_update_settings_disarms_and_cancels(self):
        self.api.set_auto_shutdown(True)
        self.api.update_settings({"shutdown_when_done": False})
        self.assertFalse(self.api.get_auto_shutdown_status()["enabled"])

    def test_cancel_endpoint_returns_a_status_payload(self):
        self.api.set_auto_shutdown(True)
        status = self.api.cancel_auto_shutdown()
        self.assertIn("state", status)
        self.assertFalse(status["enabled"])

    def test_api_shutdown_cancels_a_pending_controller(self):
        self.api.set_auto_shutdown(True)
        self.api._auto_shutdown._pending = True
        self.api.shutdown()
        self.assertGreaterEqual(self.power.cancel_count, 1)

    def test_policy_knobs_come_from_the_config(self):
        self.cfg.shutdown_allow_failures = True
        status = self.api.get_auto_shutdown_status()
        self.assertTrue(status["policy"]["allow_failures"])
        self.assertFalse(status["policy"]["allow_cancelled"])

    def test_countdown_seconds_round_trips_through_the_settings_api(self):
        self.api.update_settings({"shutdown_countdown_seconds": 15})
        self.assertEqual(self.cfg.shutdown_countdown_seconds, 15)
        # The controller reads it live, so no re-creation is needed.
        self.assertEqual(self.api._auto_shutdown._countdown_seconds(), 15)

    def test_absurd_countdown_values_are_clamped_at_use(self):
        """A hand-edited config file cannot produce an instant shutdown."""
        for value, expected in ((10 ** 9, 3600), (-5, 1), (0, 1), (15, 15)):
            self.api.update_settings({"shutdown_countdown_seconds": value})
            self.assertEqual(
                self.api._auto_shutdown._countdown_seconds(), expected, value
            )

    def test_policy_knobs_round_trip_through_the_settings_api(self):
        self.api.update_settings({
            "shutdown_allow_failures": True,
            "shutdown_allow_cancelled": True,
        })
        policy = self.api.get_auto_shutdown_status()["policy"]
        self.assertTrue(policy["allow_failures"])
        self.assertTrue(policy["allow_cancelled"])


# ── Frontend contract ───────────────────────────────────────────────────────


class FrontendContractTest(unittest.TestCase):
    """Every state/reason the backend can emit must have a UI label.

    Without this, a new state silently renders as a raw identifier.
    """

    def setUp(self):
        self.i18n = (Path(__file__).resolve().parent.parent
                     / "ui" / "frontend" / "js" / "i18n.js").read_text(
            encoding="utf-8")

    def test_every_state_has_a_label(self):
        for state in ShutdownState:
            self.assertIn('auto_shutdown.state.{}"'.format(state.value), self.i18n,
                          "missing label for state {}".format(state.value))

    def test_every_reason_has_a_label(self):
        for reason in ShutdownReason:
            if not reason.value:
                continue
            self.assertIn('auto_shutdown.reason.{}"'.format(reason.value), self.i18n,
                          "missing label for reason {}".format(reason.value))

    def test_labels_exist_in_both_locales(self):
        for state in ShutdownState:
            key = 'auto_shutdown.state.{}"'.format(state.value)
            self.assertEqual(self.i18n.count(key), 2,
                             "{} must exist in en and fa".format(key))


if __name__ == "__main__":
    unittest.main()
