"""Shared state management, persistence, and formatting for N13 UI layers.

Race-condition fixes and improvements in this rewrite
=====================================================
1.  _worker() decrement of _active is now guarded in a finally block so a
    crashed runner can never leave the active counter stuck at a high value
    (which would permanently block new downloads from starting).

2.  cancel_task() for QUEUED tasks now transitions directly to STOPPED in one
    atomic block — no window where the task could be picked up by _start_next()
    between the state read and the state write.

3.  retry_task() rebuilds TaskControl and resets SpeedMeter inside the lock
    before releasing it, so no worker can observe a half-reset record.

4.  remove_task() for an ACTIVE task: sets the removed flag and calls
    cancel_task() under a single logical sequence; the worker's finally block
    handles the "removed" event emission, eliminating the double-emit race.

5.  update_progress() now coalesces rapid updates: it only emits an event when
    at least 120 ms have elapsed or the download is 100% complete, preventing
    event floods that caused Tkinter queue back-pressure.

6.  set_max_concurrent() clamps to [1, 20] and immediately calls _start_next()
    so raising the limit mid-session unblocks waiting tasks without any delay.

7.  _save_queue_locked() uses a non-blocking try/except so a full disk or
    permission error never crashes the manager thread.

8.  History is capped at 500 entries (up from 200) and persisted atomically.

9.  SpeedMeter.update() is now monotonic-safe: it ignores backward jumps in
    completed byte count (can happen on cancel+resume).

10. TaskSnapshot.to_dict() added for convenient serialisation by the GUI.
"""
from __future__ import annotations

import enum
import json
import logging
import os
import queue as _queue_module
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Set
from urllib.parse import unquote, urlparse

from rich.console import Console


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------

_con: Optional[Console] = None

def _get_console() -> Console:
    global _con
    if _con is None:
        _con = Console()
    return _con

def _ok(msg: str)   -> None: _get_console().print(f"  [bold green]✔[/bold green]  {msg}")
def _warn(msg: str) -> None: _get_console().print(f"  [bold yellow]⚠[/bold yellow]  {msg}")
def _err(msg: str)  -> None: _get_console().print(f"  [bold red]✖[/bold red]  {msg}")
def _info(msg: str) -> None: _get_console().print(f"  [dim]ℹ[/dim]  {msg}")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def human_size(value: float) -> str:
    try:
        size = max(0.0, float(value))
    except Exception:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1024.0 or unit == "PB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def format_eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0 or seconds > 359_999:
        return "--:--"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class QueueLogHandler(logging.Handler):
    """Push log records into a queue for cross-thread UI consumption."""

    def __init__(self, q: "_queue_module.Queue[tuple[str, str]]") -> None:
        super().__init__()
        self.queue = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(("log", self.format(record)))
        except Exception:
            self.handleError(record)


def setup_logging(
    log_path: Optional[Path] = None,
    level: int = logging.INFO,
) -> logging.Logger:
    logger = logging.getLogger("n13")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------

class TaskState(str, enum.Enum):
    QUEUED      = "Queued"
    DOWNLOADING = "Downloading"
    PAUSED      = "Paused"
    STOPPING    = "Stopping"
    STOPPED     = "Stopped"
    FAILED      = "Failed"
    COMPLETED   = "Complete"


TERMINAL_STATES: frozenset[TaskState] = frozenset({
    TaskState.STOPPED,
    TaskState.FAILED,
    TaskState.COMPLETED,
})

ACTIVE_STATES: frozenset[TaskState] = frozenset({
    TaskState.DOWNLOADING,
    TaskState.PAUSED,
    TaskState.STOPPING,
})


class TaskCancelled(Exception):
    """Raised by runners when a task is cancelled."""


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    directory: str
    checksum: str = ""
    label: str = ""

    @property
    def name(self) -> str:
        if self.label:
            return self.label
        path = unquote(urlparse(self.url).path or "")
        name = Path(path).name
        if name:
            return name
        netloc = urlparse(self.url).netloc
        return netloc if netloc else self.url


@dataclass
class TaskSnapshot:
    id: str
    request: DownloadRequest
    state: TaskState
    completed: int = 0
    total: int = 0
    speed_bps: float = 0.0
    eta_seconds: Optional[float] = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def name(self) -> str:
        return self.request.name

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(100.0, max(0.0, self.completed / self.total * 100.0))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "url": self.request.url,
            "directory": self.request.directory,
            "checksum": self.request.checksum,
            "label": self.request.label,
            "state": self.state.value,
            "completed": self.completed,
            "total": self.total,
            "speed_bps": self.speed_bps,
            "eta_seconds": self.eta_seconds,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class TaskControl:
    """Cooperative pause/cancel signalling passed to download runners."""

    def __init__(self) -> None:
        self._pause = threading.Event()
        self._cancel = threading.Event()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.clear()   # unblock any waiting pause

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def wait_if_paused(self, timeout: float = 0.2) -> None:
        while self._pause.is_set() and not self._cancel.is_set():
            time.sleep(timeout)

    def raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise TaskCancelled()


# ---------------------------------------------------------------------------
# Speed meter
# ---------------------------------------------------------------------------

class SpeedMeter:
    """Exponentially-weighted moving-average speed with stale decay."""

    def __init__(self, alpha: float = 0.35, stale_after: float = 2.0) -> None:
        self._alpha = alpha
        self._stale_after = stale_after
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._last_bytes: Optional[int] = None
            self._last_time: Optional[float] = None
            self._speed: float = 0.0
            self._last_update: float = 0.0

    def update(self, completed: int, now: Optional[float] = None) -> float:
        """Record *completed* total bytes; return smoothed speed (B/s)."""
        now = now or time.monotonic()
        completed = max(0, int(completed))

        with self._lock:
            if self._last_bytes is None:
                self._last_bytes = completed
                self._last_time = now
                self._speed = 0.0
                self._last_update = now
                return 0.0

            # Ignore backward jumps (cancel+resume can reset completed to 0).
            if completed < self._last_bytes:
                self._last_bytes = completed
                self._last_time = now
                self._speed = 0.0
                self._last_update = now
                return 0.0

            elapsed = now - (self._last_time or now)
            if elapsed < 0.05:
                self._last_update = now
                return self._speed

            delta = completed - self._last_bytes
            instant = max(0.0, delta / elapsed)

            if self._speed <= 0.0:
                self._speed = instant
            else:
                self._speed = self._alpha * instant + (1.0 - self._alpha) * self._speed

            self._last_bytes = completed
            self._last_time = now
            self._last_update = now
            return self._speed

    @property
    def speed(self) -> float:
        with self._lock:
            if self._last_update and (time.monotonic() - self._last_update > self._stale_after):
                return 0.0
            return self._speed

    def eta(self, completed: int, total: int) -> Optional[float]:
        spd = self.speed
        if total > 0 and total > completed and spd > 1.0:
            return max(0.0, (total - completed) / spd)
        return None


ProgressCallback = Callable[[int, int], None]
TaskListener = Callable[[str, "TaskSnapshot"], None]


class DownloadRunner(Protocol):
    def run(
        self,
        task_id: str,
        request: DownloadRequest,
        progress: ProgressCallback,
        control: TaskControl,
    ) -> bool: ...


# ---------------------------------------------------------------------------
# Internal record
# ---------------------------------------------------------------------------

@dataclass
class _TaskRecord:
    id: str
    request: DownloadRequest
    state: TaskState = TaskState.QUEUED
    completed: int = 0
    total: int = 0
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    control: TaskControl = field(default_factory=TaskControl)
    speed: SpeedMeter = field(default_factory=SpeedMeter)
    removed: bool = False
    last_emit: float = 0.0
    thread: Optional[threading.Thread] = None


# ---------------------------------------------------------------------------
# TaskManager
# ---------------------------------------------------------------------------

class TaskManager:
    """Thread-safe download queue and state coordinator.

    Observer API
    ------------
    unsubscribe = manager.subscribe(listener)

    Listener signature:  listener(event: str, snapshot: TaskSnapshot) -> None
    Events: added | started | progress | updated | finished | removed
    """

    _HISTORY_LIMIT = 500
    _PROGRESS_EMIT_INTERVAL = 0.12   # seconds between progress events

    def __init__(
        self,
        runner: DownloadRunner,
        storage_dir: Path,
        max_concurrent: int = 1,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._runner = runner
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._queue_file   = self._storage_dir / "gui_queue.json"
        self._history_file = self._storage_dir / "gui_history.json"

        self._max_concurrent = max(1, int(max_concurrent))
        self._logger = logger or logging.getLogger("n13")

        self._lock      = threading.RLock()
        self._tasks:    Dict[str, _TaskRecord] = {}
        self._order:    List[str] = []
        self._listeners: Set[TaskListener] = set()
        self._active    = 0
        self._closed    = False
        self._global_pause = False

        self.history: List[Dict[str, Any]] = self._load_history()
        self._restore_queue()

    # ------------------------------------------------------------------
    # Observer
    # ------------------------------------------------------------------

    def subscribe(self, listener: TaskListener) -> Callable[[], None]:
        self._listeners.add(listener)
        def _unsub() -> None:
            self._listeners.discard(listener)
        return _unsub

    def _emit(self, event: str, snapshot: TaskSnapshot) -> None:
        # Snapshot the listener set before iterating — a listener is permitted
        # to call unsubscribe() inside its callback without causing a
        # RuntimeError from set modification during iteration.
        for fn in list(self._listeners):
            try:
                fn(event, snapshot)
            except Exception:
                self._logger.exception("Task listener raised")

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------

    def _snap(self, rec: _TaskRecord) -> TaskSnapshot:
        """Build a snapshot from a record. Caller must hold self._lock."""
        return TaskSnapshot(
            id=rec.id,
            request=rec.request,
            state=rec.state,
            completed=rec.completed,
            total=rec.total,
            speed_bps=rec.speed.speed,
            eta_seconds=rec.speed.eta(rec.completed, rec.total),
            error=rec.error,
            created_at=rec.created_at,
            finished_at=rec.finished_at,
        )

    def get(self, task_id: str) -> Optional[TaskSnapshot]:
        with self._lock:
            rec = self._tasks.get(task_id)
            return self._snap(rec) if rec else None

    def snapshots(self) -> List[TaskSnapshot]:
        with self._lock:
            return [
                self._snap(self._tasks[tid])
                for tid in self._order
                if tid in self._tasks
            ]

    def has_active(
        self,
        task_ids: Optional[Iterable[str]] = None,
        include_queued: bool = True,
    ) -> bool:
        wanted = set(task_ids) if task_ids is not None else None
        states = set(ACTIVE_STATES)
        if include_queued:
            states.add(TaskState.QUEUED)
        with self._lock:
            for rec in self._tasks.values():
                if wanted is not None and rec.id not in wanted:
                    continue
                if rec.state in states:
                    return True
        return False

    # ------------------------------------------------------------------
    # Queue operations
    # ------------------------------------------------------------------

    def add(
        self,
        request: DownloadRequest,
        autostart: bool = True,
        allow_duplicate: bool = False,
    ) -> str:
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            if not allow_duplicate:
                for rec in self._tasks.values():
                    if (
                        rec.request.url == request.url
                        and rec.request.directory == request.directory
                        and rec.state not in TERMINAL_STATES
                    ):
                        return rec.id
            task_id = uuid.uuid4().hex[:10]
            rec = _TaskRecord(id=task_id, request=request)
            self._tasks[task_id] = rec
            self._order.append(task_id)
            self._save_queue_locked()
            events.append(("added", self._snap(rec)))

        for ev, sn in events:
            self._emit(ev, sn)
        if autostart:
            self._start_next()
        return task_id

    def add_many(
        self,
        requests: Iterable[DownloadRequest],
        autostart: bool = False,
        allow_duplicate: bool = False,
    ) -> List[str]:
        return [self.add(r, autostart=autostart, allow_duplicate=allow_duplicate) for r in requests]

    def start_all(self) -> None:
        self._start_next()

    def start_task(self, task_id: str) -> None:
        """Start a specific QUEUED task (no-op for other states).

        The task is moved to the front of the queue so "Start now" on a card
        really starts THAT task next, not whatever happens to be oldest.
        """
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.state != TaskState.QUEUED:
                return
            try:
                self._order.remove(task_id)
            except ValueError:
                pass
            self._order.insert(0, task_id)
            self._save_queue_locked()
        self._start_next()

    @property
    def max_concurrent(self) -> int:
        with self._lock:
            return self._max_concurrent

    def set_max_concurrent(self, value: int) -> None:
        """Change the concurrent download limit and immediately unblock queued tasks."""
        with self._lock:
            self._max_concurrent = max(1, min(20, int(value)))
        self._start_next()

    def pause_task(self, task_id: str) -> None:
        event: Optional[tuple[str, TaskSnapshot]] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec and rec.state == TaskState.DOWNLOADING:
                rec.control.pause()
                rec.state = TaskState.PAUSED
                self._save_queue_locked()
                event = ("updated", self._snap(rec))
        if event:
            self._emit(*event)

    def pause_all(self) -> None:
        """Pause active downloads and block newly queued tasks from starting."""
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            self._global_pause = True
            for rec in self._tasks.values():
                if rec.state == TaskState.DOWNLOADING:
                    rec.control.pause()
                    rec.state = TaskState.PAUSED
                    events.append(("updated", self._snap(rec)))
            self._save_queue_locked()
        for ev, sn in events:
            self._emit(ev, sn)

    def resume_all(self) -> None:
        """Resume paused downloads and allow the queue to drain again."""
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            self._global_pause = False
            for rec in self._tasks.values():
                if rec.state == TaskState.PAUSED:
                    rec.control.resume()
                    rec.state = TaskState.DOWNLOADING
                    events.append(("updated", self._snap(rec)))
            self._save_queue_locked()
        for ev, sn in events:
            self._emit(ev, sn)
        self._start_next()

    def resume_task(self, task_id: str) -> None:
        event: Optional[tuple[str, TaskSnapshot]] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec and rec.state == TaskState.PAUSED:
                self._global_pause = False
                rec.control.resume()
                rec.state = TaskState.DOWNLOADING
                self._save_queue_locked()
                event = ("updated", self._snap(rec))
        if event:
            self._emit(*event)
        self._start_next()

    def cancel_task(self, task_id: str) -> None:
        """Cancel a task atomically regardless of current state."""
        event: Optional[tuple[str, TaskSnapshot]] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec:
                return
            if rec.state == TaskState.QUEUED:
                # Atomic: no window for _start_next() to pick it up.
                rec.state = TaskState.STOPPED
                rec.error = "Cancelled"
                rec.finished_at = time.time()
                self._save_queue_locked()
                event = ("finished", self._snap(rec))
            elif rec.state in (TaskState.DOWNLOADING, TaskState.PAUSED):
                rec.control.cancel()
                rec.state = TaskState.STOPPING
                self._save_queue_locked()
                event = ("updated", self._snap(rec))
        if event:
            self._emit(*event)

    def retry_task(self, task_id: str) -> None:
        event: Optional[tuple[str, TaskSnapshot]] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec and rec.state in TERMINAL_STATES:
                # Reset everything inside the lock before releasing.
                rec.state = TaskState.QUEUED
                rec.error = ""
                rec.finished_at = None
                rec.removed = False
                rec.control = TaskControl()
                rec.speed.reset()
                rec.completed = 0
                self._save_queue_locked()
                event = ("updated", self._snap(rec))
        if event:
            self._emit(*event)
            self._start_next()

    def remove_task(self, task_id: str) -> None:
        """Remove a task; cancels it first if active."""
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec:
                return
            if rec.state in ACTIVE_STATES:
                # Mark for removal; worker's finally block emits "removed".
                rec.removed = True
                # Signal the control so the worker stops promptly.
                rec.control.cancel()
                return
            # Non-active: remove immediately.
            snap = self._snap(rec)
            self._remove_record_locked(task_id)
            self._save_queue_locked()

        self._emit("removed", snap)

    def clear_finished(self) -> None:
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            for tid in list(self._order):
                rec = self._tasks.get(tid)
                if rec and rec.state in TERMINAL_STATES:
                    events.append(("removed", self._snap(rec)))
                    self._remove_record_locked(tid)
            self._save_queue_locked()
        for ev, sn in events:
            self._emit(ev, sn)

    def shutdown(self, cancel: bool = True, wait: bool = False, timeout: float = 3.0) -> None:
        with self._lock:
            self._closed = True
            ids = list(self._tasks.keys())
            threads = [r.thread for r in self._tasks.values() if r.thread]
        if cancel:
            for tid in ids:
                self.cancel_task(tid)
        if wait:
            deadline = time.time() + timeout
            for t in threads:
                if t and t.is_alive():
                    t.join(max(0.0, deadline - time.time()))

    # ------------------------------------------------------------------
    # Progress callback (called from worker threads)
    # ------------------------------------------------------------------

    def update_progress(self, task_id: str, completed: int, total: int) -> None:
        """Coalesced progress update — emits event at most every 120 ms."""
        event: Optional[tuple[str, TaskSnapshot]] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.removed:
                return
            rec.completed = max(0, int(completed))
            rec.total     = max(0, int(total))
            rec.speed.update(rec.completed)

            now   = time.monotonic()
            done  = rec.total > 0 and rec.completed >= rec.total
            if done or (now - rec.last_emit >= self._PROGRESS_EMIT_INTERVAL):
                rec.last_emit = now
                event = ("progress", self._snap(rec))

        if event:
            self._emit(*event)

    # ------------------------------------------------------------------
    # Worker management
    # ------------------------------------------------------------------

    def _start_next(self) -> None:
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            if self._closed or self._global_pause:
                return
            while self._active < self._max_concurrent:
                rec = next(
                    (
                        self._tasks[tid]
                        for tid in self._order
                        if tid in self._tasks
                        and self._tasks[tid].state == TaskState.QUEUED
                    ),
                    None,
                )
                if rec is None:
                    break
                rec.state    = TaskState.DOWNLOADING
                rec.control  = TaskControl()
                rec.speed.reset()
                rec.error    = ""
                rec.finished_at = None
                self._active += 1
                t = threading.Thread(
                    target=self._worker,
                    args=(rec.id,),
                    name=f"n13-task-{rec.id}",
                    daemon=True,
                )
                rec.thread = t
                t.start()
                events.append(("started", self._snap(rec)))

        for ev, sn in events:
            self._emit(ev, sn)

    def _worker(self, task_id: str) -> None:
        with self._lock:
            rec = self._tasks.get(task_id)
            request = rec.request if rec else None
            control = rec.control if rec else None

        if rec is None or request is None or control is None:
            with self._lock:
                self._active = max(0, self._active - 1)
            self._start_next()
            return

        ok        = False
        cancelled = False
        error     = ""

        try:
            ok = self._runner.run(
                task_id,
                request,
                lambda c, t: self.update_progress(task_id, c, t),
                control,
            )
        except TaskCancelled:
            cancelled = True
        except Exception as exc:
            error = str(exc)
            self._logger.exception("Worker thread raised")
        finally:
            # Decrement active count unconditionally to prevent counter leaks.
            events: List[tuple[str, TaskSnapshot]] = []
            with self._lock:
                self._active = max(0, self._active - 1)
                rec = self._tasks.get(task_id)
                if rec is not None:
                    stopped = rec.removed or rec.control.cancelled or cancelled
                    if stopped:
                        rec.state      = TaskState.STOPPED
                        rec.error      = rec.error or error or "Cancelled"
                        rec.finished_at = time.time()
                    elif ok:
                        rec.state      = TaskState.COMPLETED
                        rec.error      = ""
                        # Always sync completed to total on success.
                        # When total == 0 (server did not send Content-Length),
                        # keep the actual bytes-written value from the runner
                        # rather than zeroing it out.
                        if rec.total > 0:
                            rec.completed = rec.total
                        rec.finished_at = time.time()
                        self._add_history_locked(rec)
                    else:
                        rec.state      = TaskState.FAILED
                        rec.error      = rec.error or error or "Download failed"
                        rec.finished_at = time.time()
                        self._add_history_locked(rec)

                    self._save_queue_locked()

                    if rec.removed:
                        snap = self._snap(rec)
                        self._remove_record_locked(task_id)
                        events.append(("removed", snap))
                    else:
                        events.append(("finished", self._snap(rec)))

            for ev, sn in events:
                self._emit(ev, sn)

            self._start_next()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _write_json_atomic(path: Path, data: Any) -> None:
        """Write *data* to *path* atomically with fsync.

        Steps:
        1. Serialise to a temp sibling file.
        2. fsync the file data so the OS flushes kernel buffers to disk.
        3. os.replace() for an atomic rename (POSIX) / rename (Windows NTFS).

        This guarantees that a process crash or sudden power loss never leaves
        a half-written or empty JSON file; the old file is always kept until
        the new one is fully durable.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        serialised = json.dumps(data, indent=2, ensure_ascii=False)
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(serialised)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # fsync may not be supported on all file-systems (e.g. some
                    # network mounts).  That's acceptable — the rename is still
                    # better than a direct overwrite.
                    pass
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _save_queue_locked(self) -> None:
        """Persist non-completed tasks. Errors are swallowed (must not crash worker)."""
        items = []
        for tid in self._order:
            rec = self._tasks.get(tid)
            if not rec or rec.state == TaskState.COMPLETED or rec.removed:
                continue
            items.append({
                "id":        rec.id,
                "url":       rec.request.url,
                "directory": rec.request.directory,
                "checksum":  rec.request.checksum,
                "label":     rec.request.label,
                "state":     rec.state.value,
                "completed": rec.completed,
                "total":     rec.total,
                "error":     rec.error,
                "created_at":  rec.created_at,
                "finished_at": rec.finished_at,
            })
        try:
            self._write_json_atomic(self._queue_file, items)
        except OSError:
            pass   # disk full or permissions — non-fatal

    def _restore_queue(self) -> None:
        data = self._load_json(self._queue_file, [])
        if not isinstance(data, list):
            return
        with self._lock:
            for item in data:
                if not isinstance(item, dict):
                    continue
                url       = str(item.get("url", "")).strip()
                directory = str(item.get("directory", "")).strip()
                if not url or not directory:
                    continue
                try:
                    state = TaskState(str(item.get("state", TaskState.QUEUED.value)))
                except ValueError:
                    state = TaskState.QUEUED
                if state == TaskState.COMPLETED:
                    continue
                if state in (TaskState.DOWNLOADING, TaskState.PAUSED, TaskState.STOPPING):
                    state = TaskState.QUEUED
                task_id = str(item.get("id") or uuid.uuid4().hex[:10])
                if task_id in self._tasks:
                    task_id = uuid.uuid4().hex[:10]
                req = DownloadRequest(
                    url=url,
                    directory=directory,
                    checksum=str(item.get("checksum", "") or ""),
                    label=str(item.get("label", "") or ""),
                )
                rec = _TaskRecord(
                    id=task_id,
                    request=req,
                    state=state,
                    completed=_as_int(item.get("completed"), 0),
                    total=_as_int(item.get("total"), 0),
                    error=str(item.get("error", "") or ""),
                    created_at=float(item.get("created_at") or time.time()),
                    finished_at=item.get("finished_at"),
                )
                self._tasks[task_id] = rec
                self._order.append(task_id)

    def _load_history(self) -> List[Dict[str, Any]]:
        data = self._load_json(self._history_file, [])
        return data if isinstance(data, list) else []

    def _add_history_locked(self, rec: _TaskRecord) -> None:
        entry = {
            "url":        rec.request.url,
            "directory":  rec.request.directory,
            "name":       rec.request.name,
            "checksum":   rec.request.checksum,
            "size":       human_size(rec.completed or rec.total),
            "size_bytes": int(rec.completed or rec.total or 0),
            "status":     rec.state.value,
            "finished":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.history.insert(0, entry)
        self.history = self.history[: self._HISTORY_LIMIT]
        try:
            self._write_json_atomic(self._history_file, self.history)
        except OSError:
            pass

    def clear_history(self) -> None:
        with self._lock:
            self.history = []
            try:
                self._write_json_atomic(self._history_file, [])
            except OSError:
                pass

    def clear_completed_failed_history(self) -> None:
        bad = frozenset({
            TaskState.COMPLETED.value,
            TaskState.FAILED.value,
            TaskState.STOPPED.value,
        })
        with self._lock:
            self.history = [
                e for e in self.history
                if str(e.get("status", TaskState.COMPLETED.value)) not in bad
            ]
            try:
                self._write_json_atomic(self._history_file, self.history)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _remove_record_locked(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
        try:
            self._order.remove(task_id)
        except ValueError:
            pass
