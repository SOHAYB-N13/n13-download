"""Auto shutdown — an explicit, state-driven subsystem.

Why this module exists
======================
The previous implementation was one method on the web API
(``Api._maybe_auto_shutdown``) that fired on a single ``finished`` task event,
re-derived "is everything done?" from scratch, mutated the persistent user
preference as if it were runtime state, and shelled out to ``shutdown.exe``
inline.  That design could not answer any of these questions:

* Windows shutdown is already scheduled for T+60 — *what happens if the user
  starts another download now?*
* Two downloads finish at the same instant — *is one shutdown scheduled or two?*
* The queue is empty because the user removed the last task — *should we fire?*
* ``shutdown.exe`` refused the request — *is our state still honest?*
* N13 crashed during the countdown — *who cleans up the pending OS shutdown?*

This module replaces that with a small, explicit machine:

    TaskManager ──events──▶ AutoShutdownController ──▶ AutoShutdownPolicy
                                     │
                                     ├── AutoShutdownSession  (runtime state)
                                     ├── countdown timer
                                     └── PowerController ──▶ Windows

Design rules
------------
1. **Preference ≠ runtime state.**  ``AppConfig.shutdown_when_done`` is the
   *user preference* (persisted).  The live state lives in
   :class:`AutoShutdownSession` inside the controller and is never written to
   the config.  "The preference is off" and "a countdown is live" are two
   different facts.
2. **Safe failure mode.**  Every uncertain situation resolves to "do not shut
   down".  If the controller cannot read the queue, cannot talk to Windows, or
   cannot verify its own state, the machine stays on.
3. **Exactly one countdown.**  :data:`ShutdownState.COUNTDOWN` is the guard: an
   evaluation that arrives while a countdown is live is a no-op, so N
   simultaneous ``finished`` events still produce one ``shutdown /s``.
4. **Revalidate, then revalidate again.**  Eligibility is checked when the
   countdown is armed, re-checked on *every* queue event, and checked one final
   time shortly before Windows' deadline (the "final safety check").  Any
   failure of that last check aborts the pending shutdown.
5. **Testability.**  Windows is behind :class:`PowerController`; time is behind
   a timer factory.  Unit tests inject :class:`FakePowerController` and
   :class:`ManualTimerFactory`, so no test can ever shut a machine down.

Eligibility ("done") is *not* "no active downloads" — see
:class:`AutoShutdownPolicy`.  Cancelled, failed, paused, queued, retrying and
scheduled work are all distinct and are each handled explicitly.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol

from core.task import TaskStatus

log = logging.getLogger("n13")

# ── Tunables ────────────────────────────────────────────────────────────────

DEFAULT_COUNTDOWN_SECONDS = 60
MIN_COUNTDOWN_SECONDS = 1
MAX_COUNTDOWN_SECONDS = 3600

# Windows refuses `shutdown /t` values above this.
_MAX_WINDOWS_DELAY = 315_360_000

# How long before Windows' deadline the final safety check runs, so there is
# still time to issue `shutdown /a` if the check fails.
_FINAL_CHECK_GRACE = 5.0

# `shutdown.exe` comment — a fixed constant.  Never built from task metadata,
# so no task can influence the command line.
_SHUTDOWN_COMMENT = (
    "N13: all downloads finished - shutting down (run 'shutdown /a' to cancel)"
)

# Windows exit code for "no shutdown was in progress" — `shutdown /a` with
# nothing pending.  Not an error for us: cancelling is idempotent.
_NO_SHUTDOWN_IN_PROGRESS = 1116

_STATE_FILE_VERSION = 1

#: Escape hatch for tests, CI and dry runs.  When set to a truthy value the
#: real ``shutdown.exe`` controller reports itself as unsupported, so nothing in
#: the process can power the machine off.  This is a deliberate, documented
#: guard on the single highest-impact operation in the product.
NO_REAL_SHUTDOWN_ENV = "N13_NO_REAL_SHUTDOWN"


def real_shutdown_allowed() -> bool:
    """Whether this process is allowed to touch the real power state."""
    value = os.environ.get(NO_REAL_SHUTDOWN_ENV, "")
    return str(value).strip().lower() not in ("1", "true", "yes", "on")


# ── Runtime state ───────────────────────────────────────────────────────────


class ShutdownState(str, enum.Enum):
    """Live state of the auto-shutdown machine.

    ``str``-based so it can be serialised straight into the event stream and
    rendered by the UI without a translation table.
    """

    #: The user preference is off (or the platform has no power control).
    DISABLED = "Disabled"
    #: Preference on, but there is no workload to wait for yet.
    ARMED = "Armed"
    #: Preference on and work is still in progress (active/queued/paused).
    WAITING = "Waiting"
    #: Work is finished but policy forbids shutting down (e.g. failures).
    BLOCKED = "Blocked"
    #: Windows shutdown is scheduled and counting down.
    COUNTDOWN = "Countdown"
    #: A pending countdown was invalidated (or the user cancelled it).
    CANCELLED = "Cancelled"
    #: The shutdown was handed to Windows and the final check passed.
    EXECUTED = "Executed"
    #: A power operation failed; internal and OS state may disagree.
    ERROR = "Error"


class ShutdownReason(str, enum.Enum):
    """Structured reason for a *block*, a *cancellation* or an *error*.

    Structured (not free text) so the UI can translate it and logs can be
    grepped.  ``NONE`` means "no reason applies".
    """

    NONE = ""

    # Blocking reasons (the policy said "not eligible").
    NO_WORKLOAD = "no_workload"
    ACTIVE_TASK = "active_task"
    QUEUED_TASK = "queued_task"
    PAUSED_TASK = "paused_task"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    SCHEDULED_TASK_PENDING = "scheduled_task_pending"

    # Cancellation reasons (a live countdown was invalidated).
    NEW_DOWNLOAD = "new_download"
    DOWNLOAD_RESUMED = "download_resumed"
    TASK_RETRYING = "task_retrying"
    TASK_QUEUED = "task_queued"
    TASK_STARTED = "task_started"
    QUEUE_CHANGED = "queue_changed"
    USER_CANCELLED = "user_cancelled"
    USER_DISABLED = "user_disabled"
    POLICY_NOT_ELIGIBLE = "policy_not_eligible"
    FINAL_CHECK_FAILED = "final_check_failed"

    # Error / lifecycle reasons.
    SYSTEM_ERROR = "system_error"
    POWER_UNSUPPORTED = "power_unsupported"
    SCHEDULE_FAILED = "schedule_failed"
    CANCEL_FAILED = "cancel_failed"
    APP_EXITING = "app_exiting"
    STALE_SESSION = "stale_session"


#: Cancellation reasons that must *not* re-arm the shutdown automatically.
#: Without this a user-initiated cancel would be followed by an immediate
#: re-evaluation that schedules a fresh countdown — an infinite cancel/schedule
#: loop.
_DISARMING_REASONS = frozenset(
    {ShutdownReason.USER_CANCELLED, ShutdownReason.USER_DISABLED}
)


# ── Queue observation ───────────────────────────────────────────────────────


#: States that mean "a transfer is still doing something".
_ACTIVE_TRANSFER_STATES = frozenset(
    {
        TaskStatus.ANALYZING,
        TaskStatus.STARTING,
        TaskStatus.DOWNLOADING,
        TaskStatus.MERGING,
        TaskStatus.VERIFYING,
    }
)


@dataclass(frozen=True)
class QueueState:
    """A point-in-time, read-only view of the queue for the policy.

    Built from ``TaskManager.snapshots()``; the policy never touches the
    manager directly, which is what makes the rules unit-testable.
    """

    total: int = 0
    active: int = 0
    queued: int = 0
    paused: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    removed: int = 0
    #: The queue-wide scheduler window is currently gating new starts.
    scheduler_gated: bool = False
    #: Epoch seconds of a pending one-off scheduled download (legacy
    #: ``AppConfig.schedule_time``), or ``None``.
    scheduled_at: Optional[float] = None

    @property
    def blocking(self) -> int:
        """Transfers that are still in flight or waiting to be."""
        return self.active + self.queued + self.paused

    @property
    def finished(self) -> int:
        """Tasks that reached a counted terminal state in this view."""
        return self.completed + self.failed + self.cancelled

    @property
    def is_idle(self) -> bool:
        return self.blocking == 0

    @classmethod
    def from_snapshots(
        cls,
        snapshots: Iterable[Any],
        *,
        scheduler_gated: bool = False,
        scheduled_at: Optional[float] = None,
    ) -> "QueueState":
        """Count *snapshots* by state.

        ``REMOVED`` tasks are counted separately and are never treated as
        finished work — they are gone, not done.
        """
        counts: Counter = Counter()
        for snap in snapshots:
            status = getattr(snap, "state", None)
            if status is None:
                continue
            try:
                status = TaskStatus(status)
            except ValueError:
                continue
            counts[status] += 1
        return cls(
            total=sum(counts.values()),
            active=sum(counts[s] for s in _ACTIVE_TRANSFER_STATES),
            queued=counts[TaskStatus.QUEUED],
            paused=counts[TaskStatus.PAUSED],
            completed=counts[TaskStatus.COMPLETED],
            failed=counts[TaskStatus.FAILED],
            cancelled=counts[TaskStatus.CANCELLED],
            removed=counts[TaskStatus.REMOVED],
            scheduler_gated=bool(scheduler_gated),
            scheduled_at=scheduled_at,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "active": self.active,
            "queued": self.queued,
            "paused": self.paused,
            "completed": self.completed,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "removed": self.removed,
            "blocking": self.blocking,
            "scheduler_gated": self.scheduler_gated,
        }


@dataclass(frozen=True)
class SchedulerSnapshot:
    """What the controller needs to know about scheduling.

    N13's scheduler is queue-wide: it gates *existing* queued tasks inside a
    time window and never creates new downloads.  A queued task therefore
    already blocks shutdown by itself; ``gated`` exists so the UI can give an
    honest reason ("waiting for the scheduler window") instead of a generic one.

    ``scheduled_at`` covers the legacy one-off ``AppConfig.schedule_time`` used
    by the engine/CLI path — a future value there also blocks shutdown.
    """

    enabled: bool = False
    gated: bool = False
    scheduled_at: Optional[float] = None


# ── Policy ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PolicyDecision:
    """The policy's verdict plus the reason behind it."""

    eligible: bool
    reason: ShutdownReason
    queue: QueueState = field(default_factory=QueueState)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": self.reason.value,
            "queue": self.queue.to_dict(),
        }


@dataclass(frozen=True)
class AutoShutdownPolicy:
    """Decides whether the current queue state permits a shutdown.

    Explicit, small, and the only place the "what does *done* mean?" rules
    live — no business rule is allowed to hide inside an event handler.

    The rules, in evaluation order (first match wins):

    ===========================================  ===============================
    Condition                                    Verdict
    ===========================================  ===============================
    an active transfer (incl. merging/verifying) BLOCKED / ACTIVE_TASK
    a queued task                                BLOCKED / QUEUED_TASK
                                                 (SCHEDULED_TASK_PENDING when
                                                 the scheduler window is gating)
    a paused task                                BLOCKED / PAUSED_TASK
    a failed task and not ``allow_failures``      BLOCKED / TASK_FAILED
    a cancelled task and not ``allow_cancelled``  BLOCKED / TASK_CANCELLED
    a future one-off schedule                     BLOCKED / SCHEDULED_TASK_PENDING
    nothing finished yet                          BLOCKED / NO_WORKLOAD
    otherwise                                     ELIGIBLE
    ===========================================  ===============================

    ``NO_WORKLOAD`` is the anti-surprise rule: arming auto-shutdown with an
    empty queue must never power the machine off.  It also means "add a task,
    then remove it" shuts nothing down, because ``REMOVED`` is not counted as
    finished work.
    """

    #: Treat failed downloads as "the workload is done".
    allow_failures: bool = False
    #: Treat user-cancelled downloads as "the workload is done".
    allow_cancelled: bool = False

    def evaluate(
        self, queue: QueueState, *, now: Optional[float] = None
    ) -> PolicyDecision:
        now = time.time() if now is None else now

        if queue.active > 0:
            return PolicyDecision(False, ShutdownReason.ACTIVE_TASK, queue)
        if queue.queued > 0:
            reason = (
                ShutdownReason.SCHEDULED_TASK_PENDING
                if queue.scheduler_gated
                else ShutdownReason.QUEUED_TASK
            )
            return PolicyDecision(False, reason, queue)
        if queue.paused > 0:
            # A paused task is unfinished work that can resume at any moment.
            # Blocking (rather than disarming) is the safest and most
            # understandable behaviour: resume it and the countdown simply
            # starts again once it truly completes.
            return PolicyDecision(False, ShutdownReason.PAUSED_TASK, queue)
        if queue.failed > 0 and not self.allow_failures:
            return PolicyDecision(False, ShutdownReason.TASK_FAILED, queue)
        if queue.cancelled > 0 and not self.allow_cancelled:
            return PolicyDecision(False, ShutdownReason.TASK_CANCELLED, queue)
        if queue.scheduled_at is not None and queue.scheduled_at > now:
            return PolicyDecision(False, ShutdownReason.SCHEDULED_TASK_PENDING, queue)
        if queue.finished == 0:
            return PolicyDecision(False, ShutdownReason.NO_WORKLOAD, queue)
        return PolicyDecision(True, ShutdownReason.NONE, queue)


# ── Session ─────────────────────────────────────────────────────────────────


@dataclass
class AutoShutdownSession:
    """Runtime bookkeeping for one armed period.

    Purely diagnostic/lifecycle data — it exists so the state machine is
    debuggable and the UI has something real to render.  It is never persisted
    as a user preference.
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    started_at: float = field(default_factory=time.time)
    armed_at: float = field(default_factory=time.time)
    policy: AutoShutdownPolicy = field(default_factory=AutoShutdownPolicy)
    state: ShutdownState = ShutdownState.DISABLED
    countdown_seconds: int = 0
    countdown_started_at: Optional[float] = None
    countdown_deadline: Optional[float] = None
    cancel_reason: Optional[ShutdownReason] = None
    blocked_reason: Optional[ShutdownReason] = None
    last_error: str = ""
    evaluated_at: Optional[float] = None
    queue: QueueState = field(default_factory=QueueState)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "armed_at": self.armed_at,
            "state": self.state.value,
            "countdown_seconds": self.countdown_seconds,
            "countdown_started_at": self.countdown_started_at,
            "countdown_deadline": self.countdown_deadline,
            "cancel_reason": self.cancel_reason.value if self.cancel_reason else "",
            "blocked_reason": self.blocked_reason.value if self.blocked_reason else "",
            "last_error": self.last_error,
            "evaluated_at": self.evaluated_at,
            "queue": self.queue.to_dict(),
            "policy": {
                "allow_failures": self.policy.allow_failures,
                "allow_cancelled": self.policy.allow_cancelled,
            },
        }


# ── Power control ───────────────────────────────────────────────────────────


class PowerError(RuntimeError):
    """A power operation failed.  Internal state must not claim success."""


class PowerUnsupportedError(PowerError):
    """The platform has no supported power control."""


class PowerController(Protocol):
    """The only thing in N13 that may touch the operating system's power state.

    Business logic must never call ``shutdown.exe`` (or any other mechanism)
    directly: it goes through this interface so it stays testable and so the
    implementation can change without touching the state machine.
    """

    def is_supported(self) -> bool:
        """Whether this platform/controller can schedule a shutdown at all."""

    def schedule_shutdown(self, delay_seconds: int) -> None:
        """Schedule a system shutdown *delay_seconds* from now.

        Raises :class:`PowerError` if the OS refused, so the caller can keep
        its own state honest.
        """

    def cancel_shutdown(self) -> None:
        """Abort a pending system shutdown.

        Must be idempotent (nothing pending is not an error) and must raise
        :class:`PowerError` when the abort genuinely failed — the caller relies
        on that to avoid reporting a false cancellation.
        """

    def shutdown_now(self) -> None:
        """Shut down immediately (used only as an explicit fallback)."""


def _sanitize_output(value: Any, limit: int = 200) -> str:
    """Make subprocess output safe to log: single line, bounded, no NULs."""
    text = str(value or "").replace("\x00", " ").strip()
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


class WindowsPowerController:
    """Windows power control via ``shutdown.exe``.

    Uses an argument array and ``shell=False`` exclusively — no shell string is
    ever built, so no task metadata can influence the command line.  The delay
    is validated as an integer inside the documented Windows range before it is
    ever formatted into an argument.
    """

    def __init__(
        self,
        executable: str = "shutdown",
        *,
        platform: Optional[str] = None,
        timeout: float = 10.0,
        runner: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._executable = executable
        self._platform = platform if platform is not None else os.sys.platform
        self._timeout = float(timeout)
        self._runner = runner or subprocess.run

    # -- capability --------------------------------------------------------

    def is_supported(self) -> bool:
        if self._platform != "win32":
            return False
        if not real_shutdown_allowed():
            # Tests / CI / dry runs: never let this process power off a machine.
            return False
        try:
            return shutil.which(self._executable) is not None
        except Exception:
            return False

    # -- operations --------------------------------------------------------

    def schedule_shutdown(self, delay_seconds: int) -> None:
        if not self.is_supported():
            raise PowerUnsupportedError(
                "system shutdown is not supported on this platform"
            )
        delay = self._validate_delay(delay_seconds)
        proc = self._run(
            [self._executable, "/s", "/t", str(delay), "/c", _SHUTDOWN_COMMENT]
        )
        if self._returncode(proc) != 0:
            raise PowerError(
                "shutdown /s /t {} failed (exit {}): {}".format(
                    delay, self._returncode(proc), self._output(proc)
                )
            )

    def cancel_shutdown(self) -> None:
        if not self.is_supported():
            raise PowerUnsupportedError(
                "system shutdown is not supported on this platform"
            )
        proc = self._run([self._executable, "/a"])
        code = self._returncode(proc)
        if code == 0 or self._is_nothing_pending(code, proc):
            # Idempotent: there was nothing to abort.
            return
        raise PowerError(
            "shutdown /a failed (exit {}): {}".format(code, self._output(proc))
        )

    def shutdown_now(self) -> None:
        if not self.is_supported():
            raise PowerUnsupportedError(
                "system shutdown is not supported on this platform"
            )
        proc = self._run([self._executable, "/s", "/t", "0", "/c", _SHUTDOWN_COMMENT])
        if self._returncode(proc) != 0:
            raise PowerError(
                "shutdown /s /t 0 failed (exit {}): {}".format(
                    self._returncode(proc), self._output(proc)
                )
            )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _validate_delay(value: Any) -> int:
        """Coerce *value* to a valid Windows ``/t`` delay or raise."""
        try:
            delay = int(value)
        except (TypeError, ValueError):
            raise PowerError("invalid shutdown delay: {!r}".format(value)) from None
        if delay < 0 or delay > _MAX_WINDOWS_DELAY:
            raise PowerError(
                "shutdown delay {} out of range 0..{}".format(
                    delay, _MAX_WINDOWS_DELAY
                )
            )
        return delay

    def _run(self, argv: List[str]) -> Any:
        try:
            return self._runner(
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise PowerUnsupportedError(
                "{} not found: {}".format(self._executable, exc)
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise PowerError("power command timed out: {}".format(exc)) from exc
        except OSError as exc:
            raise PowerError("power command failed: {}".format(exc)) from exc

    @staticmethod
    def _returncode(proc: Any) -> int:
        try:
            return int(proc.returncode)
        except (TypeError, ValueError, AttributeError):
            return -1

    @staticmethod
    def _output(proc: Any) -> str:
        return _sanitize_output(
            getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or ""
        )

    @staticmethod
    def _is_nothing_pending(code: int, proc: Any) -> bool:
        if code == _NO_SHUTDOWN_IN_PROGRESS:
            return True
        blob = _sanitize_output(
            "{}{}".format(getattr(proc, "stderr", ""), getattr(proc, "stdout", "")),
            limit=400,
        ).lower()
        return "no shutdown" in blob or "1116" in blob


class FakePowerController:
    """In-memory :class:`PowerController` for tests and dry runs.

    Records every call and never touches the operating system, so the test
    suite can exercise the whole shutdown path deterministically.  Failure
    injection (``fail_schedule`` / ``fail_cancel``) lets tests prove the
    controller keeps its state honest when Windows refuses.
    """

    def __init__(
        self,
        *,
        supported: bool = True,
        fail_schedule: bool = False,
        fail_cancel: bool = False,
        fail_now: bool = False,
        fail_message: str = "injected failure",
    ) -> None:
        self.supported = bool(supported)
        self.fail_schedule = bool(fail_schedule)
        self.fail_cancel = bool(fail_cancel)
        self.fail_now = bool(fail_now)
        self.fail_message = fail_message
        self.calls: List[tuple] = []
        self.scheduled_delays: List[int] = []
        #: Mirrors whether the fake believes a shutdown is pending.
        self.pending = False

    # -- inspection helpers used by tests ----------------------------------

    @property
    def schedule_count(self) -> int:
        """Successful schedules (``scheduled_delays`` entries)."""
        return len(self.scheduled_delays)

    @property
    def schedule_attempts(self) -> int:
        """Every ``schedule_shutdown`` call, including the refused ones."""
        return sum(1 for c in self.calls if c[0] == "schedule_shutdown")

    @property
    def cancel_count(self) -> int:
        return sum(1 for c in self.calls if c[0] == "cancel_shutdown")

    @property
    def now_count(self) -> int:
        return sum(1 for c in self.calls if c[0] == "shutdown_now")

    # -- protocol ----------------------------------------------------------

    def is_supported(self) -> bool:
        return self.supported

    def schedule_shutdown(self, delay_seconds: int) -> None:
        self.calls.append(("schedule_shutdown", delay_seconds))
        if not self.supported:
            raise PowerUnsupportedError("unsupported (fake)")
        if self.fail_schedule:
            raise PowerError(self.fail_message)
        self.scheduled_delays.append(int(delay_seconds))
        self.pending = True

    def cancel_shutdown(self) -> None:
        self.calls.append(("cancel_shutdown",))
        if not self.supported:
            raise PowerUnsupportedError("unsupported (fake)")
        if self.fail_cancel:
            raise PowerError(self.fail_message)
        self.pending = False

    def shutdown_now(self) -> None:
        self.calls.append(("shutdown_now",))
        if not self.supported:
            raise PowerUnsupportedError("unsupported (fake)")
        if self.fail_now:
            raise PowerError(self.fail_message)
        self.pending = True


# ── Timers ──────────────────────────────────────────────────────────────────


TimerFactory = Callable[[float, Callable[[], None]], Any]


def _threading_timer(interval: float, callback: Callable[[], None]) -> Any:
    """Default timer: a daemon thread that fires once."""
    timer = threading.Timer(max(0.0, float(interval)), callback)
    timer.daemon = True
    timer.name = "n13-auto-shutdown"
    timer.start()
    return timer


class ManualTimerFactory:
    """Deterministic timer factory for tests.

    ``(interval, callback)`` calls are recorded; nothing fires until the test
    calls :meth:`fire`.  ``fire()`` runs the earliest pending timer, so a test
    can step the countdown one deadline at a time without sleeping.
    """

    def __init__(self) -> None:
        self.pending: List[Dict[str, Any]] = []
        self.cancelled = 0

    def __call__(self, interval: float, callback: Callable[[], None]) -> Any:
        entry = {"interval": float(interval), "callback": callback, "cancelled": False}
        self.pending.append(entry)
        return _ManualTimerHandle(self, entry)

    def fire(self, index: int = 0) -> bool:
        live = [e for e in self.pending if not e["cancelled"]]
        if not live:
            return False
        entry = live[index]
        self.pending.remove(entry)
        entry["callback"]()
        return True

    def fire_all(self) -> int:
        fired = 0
        while self.fire():
            fired += 1
        return fired

    @property
    def live_count(self) -> int:
        return sum(1 for e in self.pending if not e["cancelled"])


class _ManualTimerHandle:
    def __init__(self, factory: ManualTimerFactory, entry: Dict[str, Any]) -> None:
        self._factory = factory
        self._entry = entry

    def cancel(self) -> None:
        if not self._entry["cancelled"]:
            self._entry["cancelled"] = True
            self._factory.cancelled += 1


# ── Controller ──────────────────────────────────────────────────────────────


class AutoShutdownController:
    """Owns the auto-shutdown state machine, countdown and power calls.

    Thread-safe: every state read/write happens under one re-entrant lock, and
    the lock is held across the Windows call so two simultaneous
    ``task_finished`` events can never produce two ``shutdown /s`` commands.
    Listeners are always notified *outside* the lock.

    Lock ordering: this controller's lock is always taken *before*
    ``TaskManager``'s lock (via ``snapshots()``), never after —
    ``TaskManager`` emits to listeners without holding its own lock, so the
    reverse order cannot happen and there is no deadlock.
    """

    #: Minimum spacing between two consecutive *automatic* schedule attempts.
    #: Guards against a retry storm when Windows keeps refusing.
    _RESCHEDULE_COOLDOWN = 30.0

    def __init__(
        self,
        manager: Any,
        config: Any,
        *,
        power: Optional[PowerController] = None,
        logger: Optional[logging.Logger] = None,
        persist: Optional[Callable[[], None]] = None,
        scheduler: Optional[Callable[[], SchedulerSnapshot]] = None,
        state_file: Optional[Path] = None,
        timer_factory: Optional[TimerFactory] = None,
        clock: Callable[[], float] = time.time,
        countdown_grace: float = _FINAL_CHECK_GRACE,
    ) -> None:
        self._manager = manager
        self._config = config
        self._power: PowerController = power or WindowsPowerController()
        self._log = logger or log
        self._persist = persist
        self._scheduler = scheduler
        self._state_file = Path(state_file) if state_file else None
        self._timer_factory: TimerFactory = timer_factory or _threading_timer
        self._clock = clock
        self._grace = max(0.0, float(countdown_grace))

        self._lock = threading.RLock()
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._session = AutoShutdownSession()
        self._session.state = ShutdownState.DISABLED
        self._pending = False            # Windows has a shutdown scheduled
        self._timer: Any = None
        self._closed = False
        self._schedule_failed_at: Optional[float] = None
        self._last_final_check_failed = False
        # A one-shot arm raised by a *project's* completion action.  Kept apart
        # from ``shutdown_when_done`` on purpose: the preference is a standing
        # rule the user set, this is a statement about the current run only, and
        # conflating them would persist "shut down tonight" into every future
        # launch.  See ``arm_for_project_completion``.
        self._session_arm = False
        self._session_arm_project = ""

    # ------------------------------------------------------------------
    # Observation API
    # ------------------------------------------------------------------

    def subscribe(
        self, listener: Callable[[Dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Register a state-change listener; returns an unsubscribe callable."""
        with self._lock:
            self._listeners.append(listener)

        def _unsub() -> None:
            with self._lock:
                try:
                    self._listeners.remove(listener)
                except ValueError:
                    pass

        return _unsub

    def _emit(self, payload: Dict[str, Any]) -> None:
        """Notify listeners.  Never called with the lock held."""
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(payload)
            except Exception:
                self._log.exception("AutoShutdown listener raised")

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    def start(self) -> Dict[str, Any]:
        """Recover from a previous run, then evaluate.  Call once, when ready."""
        self.recover_stale_shutdown()
        with self._lock:
            self._log.info("AutoShutdown: controller started (%s)", self._describe())
            self._evaluate_locked()
            payload = self._status_locked()
        self._emit(payload)
        return payload

    def set_enabled(self, enabled: bool, *, persist: bool = True) -> Dict[str, Any]:
        """Turn the user preference on/off.

        Turning it *off* while a countdown is live cancels the pending Windows
        shutdown immediately.  The preference is written back only when the
        value actually changed.
        """
        enabled = bool(enabled)
        with self._lock:
            changed = self._preference() != enabled
            if changed and hasattr(self._config, "shutdown_when_done"):
                try:
                    setattr(self._config, "shutdown_when_done", enabled)
                except Exception:
                    pass
            if changed and persist:
                self._persist_locked()
            if not enabled:
                self._log.info("AutoShutdown: disabled by user")
                self._session_arm = False
                self._session_arm_project = ""
                ok = self._disarm_locked(
                    ShutdownReason.USER_DISABLED, cancel_pending=True
                )
                if ok:
                    self._session.state = ShutdownState.DISABLED
                    self._session.blocked_reason = None
                    self._session.cancel_reason = ShutdownReason.USER_DISABLED
            else:
                self._session.cancel_reason = None
                self._session.blocked_reason = None
                self._session.last_error = ""
                self._schedule_failed_at = None
                self._session.armed_at = self._clock()
                self._log.info("AutoShutdown: armed")
                self._evaluate_locked()
            payload = self._status_locked()
        self._emit(payload)
        return payload

    def arm_for_project_completion(self, project_id: str) -> Dict[str, Any]:
        """Arm a one-shot shutdown because a project's completion action fired.

        Two deliberate choices:

        * **Session-scoped, never persisted.**  "Shut down when this project is
          done" is a statement about *this* run.  Writing it into
          ``shutdown_when_done`` would power the machine off on some later,
          unrelated evening.
        * **It arms; it does not fire.**  The ordinary policy still decides, so
          the machine only goes down once the *whole* queue is idle.  That is
          exactly what stops one finished project from powering the box off while
          another project is still downloading — and it means there is still only
          one shutdown path, one timer, and one countdown to cancel.

        The resulting countdown is the normal one: cancellable from the UI,
        visible, and cleaned up across a restart by the usual state file.
        """
        with self._lock:
            self._session_arm = True
            self._session_arm_project = str(project_id or "")
            self._session.cancel_reason = None
            self._session.blocked_reason = None
            self._session.last_error = ""
            self._schedule_failed_at = None
            self._session.armed_at = self._clock()
            self._log.info(
                "AutoShutdown: armed by project completion (%s)",
                self._session_arm_project or "unknown",
            )
            self._evaluate_locked()
            payload = self._status_locked()
        self._emit(payload)
        return payload

    def cancel(
        self, reason: ShutdownReason = ShutdownReason.USER_CANCELLED
    ) -> Dict[str, Any]:
        """User-initiated cancellation of a pending shutdown.

        Cancels the Windows shutdown, records the reason, and — for a
        user-initiated cancel — disarms the preference so the very next
        evaluation cannot immediately schedule a fresh countdown (which would be
        an infinite loop).
        """
        with self._lock:
            ok = self._disarm_locked(reason, cancel_pending=True)
            if ok and reason in _DISARMING_REASONS:
                if hasattr(self._config, "shutdown_when_done"):
                    try:
                        setattr(self._config, "shutdown_when_done", False)
                    except Exception:
                        pass
                # A user cancel consumes the project-completion arm too, otherwise
                # the very next evaluation would raise it again.
                self._session_arm = False
                self._session_arm_project = ""
                self._persist_locked()
                self._session.state = ShutdownState.DISABLED
                self._log.info("AutoShutdown: cancelled by user — disarmed")
            elif ok:
                self._evaluate_locked()
            payload = self._status_locked()
        self._emit(payload)
        return payload

    def notify(self, event: str, snapshot: Any = None) -> None:
        """Feed a queue event in.  This is the main entry point.

        Every event that can change eligibility is routed here
        (``added``/``started``/``updated``/``finished``/``removed``), plus the
        synthetic ``queue_changed`` / ``config_changed`` / ``scheduler_changed``
        notifications.  ``progress`` is ignored: it cannot change eligibility and
        would otherwise run the policy thousands of times per transfer.
        """
        if event == "progress":
            return
        try:
            with self._lock:
                if self._closed:
                    return
                if self._session.state == ShutdownState.COUNTDOWN:
                    queue = self._queue_state_locked()
                    reason = self._invalidation_reason_locked(event, snapshot, queue)
                    if reason is None:
                        # Still eligible — the countdown stands.  Re-running the
                        # policy here could only reproduce the same verdict, and
                        # skipping it is what keeps duplicate events from
                        # scheduling a second shutdown.
                        self._evaluate_locked()
                    elif self._disarm_locked(reason, cancel_pending=True):
                        # The event itself proved the policy is not eligible, so
                        # the state stays CANCELLED until the *next* real event
                        # re-evaluates.  That makes the abort observable to the
                        # UI instead of being overwritten microseconds later.
                        self._log.info(
                            "AutoShutdown: cancelled — %s", self._reason_text(reason)
                        )
                    # else: _disarm_locked already logged and set ERROR.  Windows
                    # still owns a pending shutdown; never overwrite that with a
                    # rosier state.  The next evaluation retries the abort.
                else:
                    self._evaluate_locked()
                payload = self._status_locked()
        except Exception:
            # A manager/listener failure must never escalate into anything that
            # could touch the power state.
            self._log.exception("AutoShutdown: notify(%s) failed", event)
            return
        self._emit(payload)

    def evaluate(self) -> Dict[str, Any]:
        """Re-run the policy now (used after settings/scheduler changes)."""
        with self._lock:
            if self._closed:
                return self._status_locked()
            self._evaluate_locked()
            payload = self._status_locked()
        self._emit(payload)
        return payload

    def shutdown(self, *, reason: ShutdownReason = ShutdownReason.APP_EXITING) -> None:
        """Application teardown.

        A pending N13-scheduled shutdown is **cancelled**: once N13 has exited it
        can no longer re-validate eligibility, and the safe failure mode is
        always "the machine stays on".  Keep N13 running (minimise to tray) for a
        countdown to complete.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            had_pending = self._pending
            self._cancel_timer_locked()
            if had_pending:
                try:
                    self._power.cancel_shutdown()
                    self._log.info(
                        "AutoShutdown: cancelled — %s", self._reason_text(reason)
                    )
                except PowerError as exc:
                    self._session.last_error = _sanitize_output(exc)
                    self._log.error(
                        "AutoShutdown: failed to cancel a pending shutdown on exit: %s",
                        _sanitize_output(exc),
                    )
            self._pending = False
            self._clear_state_file_locked()
            self._session.state = ShutdownState.DISABLED
            self._session.cancel_reason = reason

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Machine-readable status for the API/UI and for tests."""
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> Dict[str, Any]:
        session = self._session
        remaining: Optional[float] = None
        if session.state == ShutdownState.COUNTDOWN and session.countdown_deadline:
            remaining = max(0.0, session.countdown_deadline - self._clock())
        if session.state in (ShutdownState.BLOCKED, ShutdownState.WAITING, ShutdownState.ARMED):
            primary = session.blocked_reason
        elif session.state in (ShutdownState.CANCELLED, ShutdownState.DISABLED):
            primary = session.cancel_reason
        elif session.state == ShutdownState.ERROR:
            primary = session.blocked_reason or ShutdownReason.SYSTEM_ERROR
        else:
            primary = None
        return {
            "enabled": self._preference(),
            "state": session.state.value,
            "supported": self._supported(),
            "pending": bool(self._pending),
            # A project-completion arm is a *session* fact, never the persisted
            # preference — the UI renders it separately so a user can tell
            # "always" from "just for this project".
            "session_arm": bool(self._session_arm),
            "armed_by_project": self._session_arm_project,
            "countdown_seconds": session.countdown_seconds,
            "seconds_remaining": remaining,
            "deadline": session.countdown_deadline,
            "reason": primary.value if primary else "",
            "cancel_reason": session.cancel_reason.value if session.cancel_reason else "",
            "blocked_reason": (
                session.blocked_reason.value if session.blocked_reason else ""
            ),
            "last_error": session.last_error,
            # The *live* policy, so the UI can explain the rules without waiting
            # for an evaluation to happen.  ``session.policy`` is the snapshot
            # taken at the last evaluation and is kept for diagnostics.
            "policy": {
                "allow_failures": self._policy().allow_failures,
                "allow_cancelled": self._policy().allow_cancelled,
            },
            "session": session.to_dict(),
        }

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _supported(self) -> bool:
        """Whether power control is available.  Deliberately not cached: the
        answer is cheap and a stale cache here would mean acting on a wrong
        belief about the machine's power state."""
        try:
            return bool(self._power.is_supported())
        except Exception:
            return False

    def _preference(self) -> bool:
        return bool(getattr(self._config, "shutdown_when_done", False))

    def _policy(self) -> AutoShutdownPolicy:
        return AutoShutdownPolicy(
            allow_failures=bool(getattr(self._config, "shutdown_allow_failures", False)),
            allow_cancelled=bool(
                getattr(self._config, "shutdown_allow_cancelled", False)
            ),
        )

    def _countdown_seconds(self) -> int:
        raw = getattr(
            self._config, "shutdown_countdown_seconds", DEFAULT_COUNTDOWN_SECONDS
        )
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = DEFAULT_COUNTDOWN_SECONDS
        return max(MIN_COUNTDOWN_SECONDS, min(MAX_COUNTDOWN_SECONDS, value))

    def _scheduler_snapshot(self) -> SchedulerSnapshot:
        if self._scheduler is None:
            return SchedulerSnapshot()
        try:
            snap = self._scheduler()
        except Exception:
            # An unreadable scheduler must never make us *more* willing to shut
            # down, so report it as gating.
            self._log.exception("AutoShutdown: scheduler snapshot failed")
            return SchedulerSnapshot(gated=True)
        return snap if isinstance(snap, SchedulerSnapshot) else SchedulerSnapshot()

    def _queue_state_locked(self) -> QueueState:
        """Read the queue.  On failure, report a blocking state (fail safe)."""
        try:
            snaps = self._manager.snapshots()
        except Exception:
            self._log.exception("AutoShutdown: could not read the queue")
            return QueueState(active=1)
        sched = self._scheduler_snapshot()
        scheduled_at = sched.scheduled_at
        if scheduled_at is None:
            scheduled_at = self._one_off_schedule_at()
        return QueueState.from_snapshots(
            snaps,
            scheduler_gated=bool(sched.enabled and sched.gated),
            scheduled_at=scheduled_at,
        )

    def _one_off_schedule_at(self) -> Optional[float]:
        """Epoch seconds of a future one-off ``schedule_time``, else ``None``."""
        getter = getattr(self._config, "get_schedule_datetime", None)
        if not callable(getter):
            return None
        try:
            target = getter()
        except Exception:
            return None
        if target is None:
            return None
        try:
            return float(target.timestamp())
        except Exception:
            return None

    def _evaluate_locked(self) -> None:
        """The single evaluation entry point (see the module docstring)."""
        self._session.evaluated_at = self._clock()
        self._session.policy = self._policy()

        # A pending OS shutdown we could not cancel: never overwrite it with a
        # rosier state, and keep retrying the cancellation.
        if self._pending and self._session.state == ShutdownState.ERROR:
            if not self._retry_cancel_locked():
                return
            # The abort finally succeeded.  Stop here so the CANCELLED state is
            # observable; the next event settles the machine into its real
            # waiting/armed state.
            return

        if not (self._preference() or self._session_arm):
            if self._pending:
                self._disarm_locked(ShutdownReason.USER_DISABLED, cancel_pending=True)
            self._session.state = ShutdownState.DISABLED
            self._session.blocked_reason = None
            return

        if not self._supported():
            if self._pending:
                self._disarm_locked(
                    ShutdownReason.POWER_UNSUPPORTED, cancel_pending=True
                )
            self._session.state = ShutdownState.ERROR
            self._session.blocked_reason = ShutdownReason.POWER_UNSUPPORTED
            self._session.last_error = "system shutdown is not supported here"
            return

        queue = self._queue_state_locked()
        self._session.queue = queue
        decision = self._policy().evaluate(queue, now=self._clock())

        if self._session.state == ShutdownState.COUNTDOWN:
            # A live countdown stays live while the policy still says eligible.
            # That is what makes "task removed" (Case F) a no-op instead of a
            # cancel/re-schedule churn, and what keeps duplicate events from
            # scheduling a second shutdown.
            if decision.eligible:
                return
            if not self._disarm_locked(decision.reason, cancel_pending=True):
                return
            self._log.info(
                "AutoShutdown: cancelled — %s", self._reason_text(decision.reason)
            )

        if decision.eligible:
            self._start_countdown_locked(queue)
            return

        self._session.blocked_reason = decision.reason
        self._session.state = self._state_for_blocked(decision.reason)
        if decision.reason == ShutdownReason.NO_WORKLOAD:
            self._schedule_failed_at = None

    @staticmethod
    def _state_for_blocked(reason: ShutdownReason) -> ShutdownState:
        """Map a blocking reason onto a state the UI can render meaningfully."""
        if reason == ShutdownReason.NO_WORKLOAD:
            # Armed, but there is nothing to wait for yet.
            return ShutdownState.ARMED
        if reason in (
            ShutdownReason.ACTIVE_TASK,
            ShutdownReason.QUEUED_TASK,
            ShutdownReason.PAUSED_TASK,
            ShutdownReason.SCHEDULED_TASK_PENDING,
        ):
            return ShutdownState.WAITING
        return ShutdownState.BLOCKED

    # ------------------------------------------------------------------
    # Countdown
    # ------------------------------------------------------------------

    def _start_countdown_locked(self, queue: QueueState) -> None:
        # Never two countdowns.  (The callers already guarantee this, but the
        # guard is cheap and makes the invariant local to the scheduling site.)
        if self._pending or self._session.state == ShutdownState.COUNTDOWN:
            return
        now = self._clock()
        if (
            self._schedule_failed_at is not None
            and (now - self._schedule_failed_at) < self._RESCHEDULE_COOLDOWN
        ):
            # Windows refused recently; do not hammer it.  A fresh workload
            # (which resets the latch through NO_WORKLOAD) or the cooldown
            # expiring allows another attempt.
            self._session.state = ShutdownState.ERROR
            return
        seconds = self._countdown_seconds()
        self._session.countdown_seconds = seconds
        self._session.countdown_started_at = now
        self._session.countdown_deadline = now + seconds
        self._session.cancel_reason = None
        self._session.blocked_reason = None
        self._session.last_error = ""
        self._session.queue = queue

        # Write the crash-recovery marker BEFORE asking Windows.  A crash
        # between the two steps then leaves a marker with no pending shutdown,
        # which startup recovery clears harmlessly — the opposite order could
        # leave a pending shutdown nobody knows about.
        self._write_state_file_locked()

        self._log.info("AutoShutdown: countdown started — %ss", seconds)
        try:
            self._power.schedule_shutdown(seconds)
        except PowerError as exc:
            self._pending = False
            self._schedule_failed_at = now
            self._clear_state_file_locked()
            self._cancel_timer_locked()
            self._session.state = ShutdownState.ERROR
            self._session.last_error = _sanitize_output(exc)
            self._session.blocked_reason = (
                ShutdownReason.POWER_UNSUPPORTED
                if isinstance(exc, PowerUnsupportedError)
                else ShutdownReason.SCHEDULE_FAILED
            )
            self._log.error(
                "AutoShutdown: failed to schedule the shutdown — %s",
                _sanitize_output(exc),
            )
            return

        self._pending = True
        self._schedule_failed_at = None
        self._session.state = ShutdownState.COUNTDOWN
        self._log.info("AutoShutdown: shutdown scheduled (Windows)")
        self._arm_final_check_locked(seconds)

    def _arm_final_check_locked(self, seconds: int) -> None:
        """Schedule the final safety check just before Windows' deadline."""
        self._cancel_timer_locked()
        # Never let the grace swallow the whole countdown: with a short
        # countdown a full-size grace would fire the check immediately and
        # leave the deadline itself unverified.
        grace = min(self._grace, float(seconds) * 0.5)
        interval = max(0.0, float(seconds) - grace)
        try:
            self._timer = self._timer_factory(interval, self._on_final_check)
        except Exception:
            # Without a timer we lose the pre-deadline re-validation, which is a
            # safety net — treat its absence as unsafe and abort rather than let
            # an unverified shutdown stand.
            self._log.exception("AutoShutdown: could not arm the final check")
            self._disarm_locked(ShutdownReason.SYSTEM_ERROR, cancel_pending=True)

    def _on_final_check(self) -> None:
        """Final safety check — the last thing that runs before Windows fires.

        Re-reads the queue and re-runs the policy.  Anything short of "still
        eligible" aborts the pending shutdown.
        """
        with self._lock:
            if self._closed or self._session.state != ShutdownState.COUNTDOWN:
                return
            self._timer = None
            queue = self._queue_state_locked()
            self._session.queue = queue
            decision = self._policy().evaluate(queue, now=self._clock())
            if not decision.eligible:
                self._last_final_check_failed = True
                self._log.warning(
                    "AutoShutdown: final safety check failed — %s",
                    self._reason_text(decision.reason),
                )
                if self._disarm_locked(
                    ShutdownReason.FINAL_CHECK_FAILED, cancel_pending=True
                ):
                    self._session.blocked_reason = decision.reason
                    self._session.state = self._state_for_blocked(decision.reason)
                payload = self._status_locked()
            else:
                self._last_final_check_failed = False
                self._log.info("AutoShutdown: final safety check passed")
                # Windows already owns the timer; there is nothing more to do
                # but record it.  Auto shutdown is one-shot: the preference is
                # consumed by firing.
                self._session.state = ShutdownState.EXECUTED
                self._session.cancel_reason = None
                self._pending = False
                self._clear_state_file_locked()
                self._disarm_preference_locked()
                payload = self._status_locked()
        self._emit(payload)

    # ------------------------------------------------------------------
    # Cancellation / disarming
    # ------------------------------------------------------------------

    def _disarm_locked(
        self, reason: ShutdownReason, *, cancel_pending: bool
    ) -> bool:
        """Stop a live countdown and (optionally) abort the OS shutdown.

        Returns ``False`` when the OS refused to abort — the caller must then
        keep reporting the un-cancelled state instead of pretending success.
        """
        self._cancel_timer_locked()
        if cancel_pending and self._pending:
            try:
                self._power.cancel_shutdown()
                self._log.info("AutoShutdown: Windows shutdown cancellation succeeded")
                self._pending = False
                self._clear_state_file_locked()
            except PowerError as exc:
                # The abort failed: Windows will still shut down.  Report the
                # truth instead of pretending the cancellation worked.
                self._pending = True
                self._session.state = ShutdownState.ERROR
                self._session.last_error = _sanitize_output(exc)
                self._session.cancel_reason = ShutdownReason.CANCEL_FAILED
                self._log.error(
                    "AutoShutdown: Windows shutdown cancellation failed — %s",
                    _sanitize_output(exc),
                )
                return False
        else:
            self._pending = False
            self._clear_state_file_locked()
        if self._session.state == ShutdownState.COUNTDOWN:
            self._session.state = ShutdownState.CANCELLED
        self._session.cancel_reason = reason
        self._session.countdown_started_at = None
        self._session.countdown_deadline = None
        self._session.countdown_seconds = 0
        return True

    def _retry_cancel_locked(self) -> bool:
        """Retry a previously failed cancellation.  ``True`` when resolved."""
        try:
            self._power.cancel_shutdown()
        except PowerError as exc:
            self._session.state = ShutdownState.ERROR
            self._session.last_error = _sanitize_output(exc)
            return False
        self._pending = False
        self._clear_state_file_locked()
        self._session.last_error = ""
        self._session.state = ShutdownState.CANCELLED
        self._log.info("AutoShutdown: Windows shutdown cancellation succeeded (retry)")
        return True

    def _disarm_preference_locked(self) -> None:
        """One-shot completion: the user preference is consumed by firing."""
        self._session_arm = False
        self._session_arm_project = ""
        if hasattr(self._config, "shutdown_when_done"):
            try:
                setattr(self._config, "shutdown_when_done", False)
            except Exception:
                return
        self._persist_locked()

    def _cancel_timer_locked(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _invalidation_reason_locked(
        self, event: str, snapshot: Any, queue: QueueState
    ) -> Optional[ShutdownReason]:
        """Why a live countdown must be aborted, or ``None`` to keep it.

        Correctness does not depend on the event name: the policy is the
        authority.  The event name only makes the reason more precise, which
        matters for the logs and for what the UI tells the user.
        """
        decision = self._policy().evaluate(queue, now=self._clock())
        if decision.eligible:
            return None
        return self._refine_reason(event, snapshot, decision.reason)

    @staticmethod
    def _refine_reason(
        event: str, snapshot: Any, fallback: ShutdownReason
    ) -> ShutdownReason:
        state = getattr(snapshot, "state", None)
        if event == "added":
            return ShutdownReason.NEW_DOWNLOAD
        if event == "started":
            return ShutdownReason.TASK_STARTED
        if event == "updated":
            if state == TaskStatus.QUEUED:
                return ShutdownReason.TASK_RETRYING
            if state == TaskStatus.DOWNLOADING:
                return ShutdownReason.DOWNLOAD_RESUMED
            if state == TaskStatus.PAUSED:
                return ShutdownReason.PAUSED_TASK
        if event == "removed" and fallback == ShutdownReason.NO_WORKLOAD:
            # Removing the last counted task leaves nothing to wait for.
            return ShutdownReason.QUEUE_CHANGED
        return fallback

    # ------------------------------------------------------------------
    # Crash-recovery marker
    # ------------------------------------------------------------------

    def _write_state_file_locked(self) -> None:
        if self._state_file is None:
            return
        payload = {
            "version": _STATE_FILE_VERSION,
            "pid": os.getpid(),
            "session_id": self._session.session_id,
            "state": ShutdownState.COUNTDOWN.value,
            "delay_seconds": self._session.countdown_seconds,
            "scheduled_at": self._clock(),
            "deadline": self._session.countdown_deadline,
        }
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self._state_file)
        except OSError as exc:
            self._log.warning(
                "AutoShutdown: could not write the recovery marker: %s",
                _sanitize_output(exc),
            )

    def _clear_state_file_locked(self) -> None:
        if self._state_file is None:
            return
        try:
            self._state_file.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            self._log.warning(
                "AutoShutdown: could not clear the recovery marker: %s",
                _sanitize_output(exc),
            )

    def _read_state_file(self) -> Optional[Dict[str, Any]]:
        if self._state_file is None:
            return None
        try:
            raw = self._state_file.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def recover_stale_shutdown(self) -> Optional[ShutdownReason]:
        """Clean up a pending shutdown left behind by a previous run.

        The marker is only written while a countdown is live and is removed on
        clean exit, so its presence means the previous process died (or was
        killed) mid-countdown.  Windows may still be counting down; cancelling
        is the safe direction, and "nothing pending" is treated as success.

        Returns the reason if recovery acted, else ``None``.
        """
        with self._lock:
            data = self._read_state_file()
            if not data:
                return None
            if str(data.get("state") or "") != ShutdownState.COUNTDOWN.value:
                self._clear_state_file_locked()
                return None
            if not self._supported():
                # Nothing we can do on this platform; do not keep retrying.
                self._clear_state_file_locked()
                return None
            try:
                self._power.cancel_shutdown()
            except PowerError as exc:
                # Keep the marker so the next launch retries.
                self._log.error(
                    "AutoShutdown: could not clear a stale pending shutdown: %s",
                    _sanitize_output(exc),
                )
                return ShutdownReason.SYSTEM_ERROR
            self._clear_state_file_locked()
            self._log.info(
                "AutoShutdown: cleared a stale pending shutdown from a previous run"
            )
            return ShutdownReason.STALE_SESSION

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _persist_locked(self) -> None:
        if self._persist is None:
            return
        try:
            self._persist()
        except Exception:
            self._log.warning("AutoShutdown: could not persist the preference")

    def _reason_text(self, reason: ShutdownReason) -> str:
        return {
            ShutdownReason.NEW_DOWNLOAD: "new download added",
            ShutdownReason.DOWNLOAD_RESUMED: "a download was resumed",
            ShutdownReason.TASK_RETRYING: "a retry became pending",
            ShutdownReason.TASK_QUEUED: "a task became queued",
            ShutdownReason.TASK_STARTED: "a task started",
            ShutdownReason.QUEUE_CHANGED: "the queue changed",
            ShutdownReason.USER_CANCELLED: "cancelled by the user",
            ShutdownReason.USER_DISABLED: "auto shutdown was turned off",
            ShutdownReason.POLICY_NOT_ELIGIBLE: "the policy no longer allows it",
            ShutdownReason.FINAL_CHECK_FAILED: "the final safety check failed",
            ShutdownReason.ACTIVE_TASK: "active download(s) remain",
            ShutdownReason.QUEUED_TASK: "queued download(s) remain",
            ShutdownReason.PAUSED_TASK: "paused download(s) remain",
            ShutdownReason.TASK_FAILED: "failed download(s) remain",
            ShutdownReason.TASK_CANCELLED: "cancelled download(s) remain",
            ShutdownReason.SCHEDULED_TASK_PENDING: "a scheduled download is pending",
            ShutdownReason.NO_WORKLOAD: "there is no finished download to wait for",
            ShutdownReason.SYSTEM_ERROR: "a system error occurred",
            ShutdownReason.POWER_UNSUPPORTED: "system shutdown is unsupported",
            ShutdownReason.SCHEDULE_FAILED: "Windows refused the shutdown",
            ShutdownReason.CANCEL_FAILED: "Windows refused the cancellation",
            ShutdownReason.APP_EXITING: "N13 is exiting",
            ShutdownReason.STALE_SESSION: "stale session from a previous run",
            ShutdownReason.NONE: "",
        }.get(reason, reason.value)

    def _describe(self) -> str:
        return "preference={} supported={}".format(self._preference(), self._supported())

    # -- test/debug affordances -------------------------------------------

    @property
    def state(self) -> ShutdownState:
        with self._lock:
            return self._session.state

    @property
    def session(self) -> AutoShutdownSession:
        with self._lock:
            return replace(self._session)

    @property
    def pending(self) -> bool:
        with self._lock:
            return self._pending

    @property
    def final_check_failed(self) -> bool:
        with self._lock:
            return self._last_final_check_failed

    def seconds_remaining(self) -> Optional[float]:
        return self.status()["seconds_remaining"]
