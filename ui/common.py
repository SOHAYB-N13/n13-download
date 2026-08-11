"""Shared state management, persistence, and formatting for N13 UI layers.

This is the queue layer for both the TUI and the GUI.  It now sits on top of
:class:`core.task.DownloadTask` (a strict state machine) and persists every
transition to :class:`core.store.TaskStore` (SQLite), replacing the old
``gui_queue.json``/``gui_history.json`` pair.

Design invariants
=================
1. A task has exactly one ``DownloadTask.status`` — no scattered boolean flags.
2. Every state change is validated against the state-machine transition table;
   the worker thread uses ``force_status`` *only* when recording a terminal
   outcome, never for normal progression.
3. Pause/cancel is per-task (``core.control.TaskControl``) — the old process
   global ``DownloadContext`` is no longer shared between concurrent tasks.
4. All persistence writes are best-effort (a disk-full / permission error must
   never crash a worker or the manager thread).
"""

from __future__ import annotations

import json
import logging
import os
import queue as _queue_module
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Set
from urllib.parse import unquote, urlparse

from rich.console import Console

from core.control import TaskCancelled, TaskControl
from core.store import TaskStore
from core.task import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    DownloadTask,
    TaskStatus,
    TransitionError,
    is_terminal,
    normalize_status,
)

# Re-export for callers that reference the legacy name (ui.api etc.).
TaskState = TaskStatus


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------

_con: Optional[Console] = None


def _get_console() -> Console:
    global _con
    if _con is None:
        _con = Console()
    return _con


def _ok(msg: str) -> None:
    _get_console().print(f"  [bold green]✔[/bold green]  {msg}")


def _warn(msg: str) -> None:
    _get_console().print(f"  [bold yellow]⚠[/bold yellow]  {msg}")


def _err(msg: str) -> None:
    _get_console().print(f"  [bold red]✖[/bold red]  {msg}")


def _info(msg: str) -> None:
    _get_console().print(f"  [dim]ℹ[/dim]  {msg}")


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


def name_from_url(url: str, label: str = "") -> str:
    """Best-effort file name from a URL (used when no server filename yet)."""
    if label:
        return label
    try:
        path = unquote(urlparse(url).path or "")
        name = Path(path).name
        if name:
            return name
    except Exception:
        pass
    netloc = urlparse(url).netloc
    return netloc if netloc else url


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
# Task model (UI-facing snapshot)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DownloadRequest:
    url: str
    directory: str
    checksum: str = ""
    label: str = ""
    category: str = "General"
    priority: int = 5
    speed_limit_bps: int = 0

    @property
    def name(self) -> str:
        return name_from_url(self.url, self.label)


@dataclass
class TaskSnapshot:
    id: str
    request: DownloadRequest
    state: TaskStatus
    completed: int = 0
    total: int = 0
    speed_bps: float = 0.0
    eta_seconds: Optional[float] = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    # Extended task metadata.
    priority: int = 5
    retry_count: int = 0
    connections: int = 1
    content_type: str = ""
    server: str = ""
    supports_range: bool = False
    category: str = "General"
    average_speed: float = 0.0
    filename: str = ""
    started_at: Optional[float] = None

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
            "priority": self.priority,
            "retry_count": self.retry_count,
            "connections": self.connections,
            "content_type": self.content_type,
            "server": self.server,
            "supports_range": bool(self.supports_range),
            "category": self.category,
            "average_speed": self.average_speed,
            "filename": self.filename,
            "started_at": self.started_at,
        }


ProgressCallback = Callable[[int, int], None]
TaskListener = Callable[[str, "TaskSnapshot"], None]


class DownloadRunner(Protocol):
    """Runners that execute one task's analyze + transfer phases."""

    def analyze(self, task_id: str, request: DownloadRequest, control: TaskControl) -> Any:
        ...  # pragma: no cover

    def download(
        self,
        task_id: str,
        request: DownloadRequest,
        analysis: Any,
        progress: ProgressCallback,
        control: TaskControl,
        status_callback: Optional[Callable[[str], None]] = None,
        path_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Internal record
# ---------------------------------------------------------------------------

@dataclass
class _TaskRecord:
    task: DownloadTask
    control: TaskControl = field(default_factory=TaskControl)
    removed: bool = False
    last_emit: float = 0.0
    thread: Optional[threading.Thread] = None
    # Set during app shutdown so the worker finalize preserves the persisted
    # resumable state instead of marking the task CANCELLED.
    shutting_down: bool = False

    @property
    def id(self) -> str:
        return self.task.id


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

    _PROGRESS_EMIT_INTERVAL = 0.12  # seconds between progress events

    def __init__(
        self,
        runner: DownloadRunner,
        storage_dir: Path,
        max_concurrent: int = 1,
        logger: Optional[logging.Logger] = None,
        config=None,
    ) -> None:
        self._runner = runner
        self._config = config
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._store = TaskStore(self._storage_dir / "downloads.db")
        # Legacy JSON files, used only as a one-time migration source.
        self._queue_file = self._storage_dir / "gui_queue.json"
        self._history_file = self._storage_dir / "gui_history.json"

        self._max_concurrent = max(1, int(max_concurrent))
        self._logger = logger or logging.getLogger("n13")

        self._lock = threading.RLock()
        self._tasks: Dict[str, _TaskRecord] = {}
        self._order: List[str] = []
        self._listeners: Set[TaskListener] = set()
        self._active = 0
        self._closed = False
        self._global_pause = False
        self._scheduler_gate = False

        self.history: List[Dict[str, Any]] = self._load_history()
        self._restore_queue()
        # Crash recovery: validate partial files and requeue unfinished tasks.
        self.recover_unfinished()
        # Optionally continue restored downloads immediately.
        if config is not None and bool(getattr(config, "resume_on_startup", False)):
            self._start_next()

    # ------------------------------------------------------------------
    # Observer
    # ------------------------------------------------------------------

    def subscribe(self, listener: TaskListener) -> Callable[[], None]:
        self._listeners.add(listener)

        def _unsub() -> None:
            self._listeners.discard(listener)

        return _unsub

    def _emit(self, event: str, snapshot: TaskSnapshot) -> None:
        for fn in list(self._listeners):
            try:
                fn(event, snapshot)
            except Exception:
                self._logger.exception("Task listener raised")

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _request_for(task: DownloadTask) -> DownloadRequest:
        return DownloadRequest(
            url=task.url,
            directory=task.directory,
            checksum=task.checksum,
            label=task.label,
            category=task.category,
            priority=task.priority,
            speed_limit_bps=task.speed_limit_bps,
        )

    def _snap(self, rec: _TaskRecord) -> TaskSnapshot:
        t = rec.task
        return TaskSnapshot(
            id=t.id,
            request=self._request_for(t),
            state=t.status,
            completed=t.downloaded_size,
            total=t.total_size,
            speed_bps=t.current_speed,
            eta_seconds=t.eta_seconds,
            error=t.error,
            created_at=t.created_at,
            finished_at=t.completed_at,
            priority=t.priority,
            retry_count=t.retry_count,
            connections=t.connections,
            content_type=t.content_type,
            server=t.server,
            supports_range=t.supports_range,
            category=t.category,
            average_speed=t.average_speed,
            filename=t.filename,
            started_at=t.started_at,
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
            states.add(TaskStatus.QUEUED)
        with self._lock:
            for tid in self._order:
                rec = self._tasks.get(tid)
                if not rec:
                    continue
                if wanted is not None and rec.id not in wanted:
                    continue
                if rec.task.status in states:
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
                for tid in self._order:
                    rec = self._tasks.get(tid)
                    if (
                        rec is not None
                        and rec.task.url == request.url
                        and rec.task.directory == request.directory
                        and not is_terminal(rec.task.status)
                    ):
                        return rec.id
            task = DownloadTask(
                url=request.url,
                directory=request.directory,
                checksum=request.checksum,
                label=request.label,
                category=self._resolve_category(request),
                priority=request.priority,
                speed_limit_bps=request.speed_limit_bps,
                filename=request.label,
                autostart=autostart,
            )
            task_id = task.id
            rec = _TaskRecord(task=task)
            self._tasks[task_id] = rec
            self._order.append(task_id)
            self._save_task_locked(rec)
            events.append(("added", self._snap(rec)))

        for ev, sn in events:
            self._emit(ev, sn)
        if autostart:
            self._start_next()
        return task_id

    def _detect_category(self, request: DownloadRequest) -> str:
        """Auto-assign a category from the request when enabled."""
        if self._config is None or not getattr(self._config, "auto_categorize", True):
            return "General"
        from core.analyzer import detect_category

        hint = request.label or name_from_url(request.url)
        ext_map = getattr(self._config, "category_extensions", None) or {}
        return detect_category(hint, "", ext_map=ext_map)

    def _resolve_category(self, request: DownloadRequest) -> str:
        """Category for a new task.

        ``"General"`` is the *unset* default (the Add dialog passes the real
        category explicitly when it knows one), so an unset category falls back
        to auto-detection when ``auto_categorize`` is enabled.
        """
        if request.category and request.category != "General":
            return request.category
        return self._detect_category(request)

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
        """Start a specific QUEUED task (no-op for other states)."""
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.task.status != TaskStatus.QUEUED:
                return
            try:
                self._order.remove(task_id)
            except ValueError:
                pass
            self._order.insert(0, task_id)
            self._save_order_locked()
        self._start_next()

    @property
    def max_concurrent(self) -> int:
        with self._lock:
            return self._max_concurrent

    def set_max_concurrent(self, value: int) -> None:
        with self._lock:
            self._max_concurrent = max(1, min(20, int(value)))
        self._start_next()

    # ── Priority / ordering ──────────────────────────────────────────

    def set_priority(self, task_id: str, priority: int) -> None:
        """Set a task's priority (0 = highest, 10 = lowest)."""
        event: Optional[tuple[str, TaskSnapshot]] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec:
                rec.task.priority = max(0, min(10, int(priority)))
                self._save_task_locked(rec)
                event = ("updated", self._snap(rec))
        if event:
            self._emit(*event)

    def move_task(self, task_id: str, delta: int) -> None:
        """Move a task up (-1) or down (+1) in the queue order."""
        with self._lock:
            try:
                idx = self._order.index(task_id)
            except ValueError:
                return
            new_idx = max(0, min(len(self._order) - 1, idx + delta))
            if new_idx == idx:
                return
            self._order.pop(idx)
            self._order.insert(new_idx, task_id)
            self._save_order_locked()

    def retry_failed(self) -> int:
        """Re-queue every failed/cancelled task; returns how many."""
        with self._lock:
            ids = [
                tid
                for tid in self._order
                if self._tasks.get(tid) is not None
                and self._tasks[tid].task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED)
            ]
        for tid in ids:
            self.retry_task(tid)
        return len(ids)

    def clear_failed(self) -> int:
        """Remove failed/cancelled tasks from the list; returns count removed."""
        return self._clear_states([TaskStatus.FAILED, TaskStatus.CANCELLED])

    def clear_completed(self) -> int:
        """Remove completed tasks from the list; returns count removed."""
        return self._clear_states([TaskStatus.COMPLETED])

    def _clear_states(self, states: Iterable[TaskStatus]) -> int:
        wanted = set(states)
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            for tid in list(self._order):
                rec = self._tasks.get(tid)
                if rec and rec.task.status in wanted:
                    events.append(("removed", self._snap(rec)))
                    self._remove_record_locked(tid)
            self._save_order_locked()
        for ev, sn in events:
            self._emit(ev, sn)
        return len(events)

    # ── Pause / resume / cancel / retry / remove ─────────────────────

    def pause_task(self, task_id: str) -> None:
        event: Optional[tuple[str, TaskSnapshot]] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec and rec.task.status in ACTIVE_STATES:
                rec.control.pause()
                if rec.task.status == TaskStatus.DOWNLOADING:
                    try:
                        rec.task.transition(TaskStatus.PAUSED)
                    except TransitionError:
                        pass
                self._save_task_locked(rec)
                event = ("updated", self._snap(rec))
        if event:
            self._emit(*event)

    def set_scheduler_gate(self, on: bool) -> None:
        """Temporarily block new downloads from starting (scheduler window)."""
        with self._lock:
            self._scheduler_gate = bool(on)
        if not on:
            self._start_next()

    def pause_all(self) -> None:
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            self._global_pause = True
            for tid in self._order:
                rec = self._tasks.get(tid)
                if not rec or rec.task.status not in ACTIVE_STATES:
                    continue
                rec.control.pause()
                if rec.task.status == TaskStatus.DOWNLOADING:
                    try:
                        rec.task.transition(TaskStatus.PAUSED)
                    except TransitionError:
                        pass
                events.append(("updated", self._snap(rec)))
            self._save_order_locked()
        for ev, sn in events:
            self._emit(ev, sn)

    def resume_all(self) -> None:
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            self._global_pause = False
            for tid in self._order:
                rec = self._tasks.get(tid)
                if not rec or rec.task.status != TaskStatus.PAUSED:
                    continue
                rec.control.resume()
                try:
                    rec.task.transition(TaskStatus.DOWNLOADING)
                except TransitionError:
                    pass
                events.append(("updated", self._snap(rec)))
        for ev, sn in events:
            self._emit(ev, sn)
        self._start_next()

    def resume_task(self, task_id: str) -> None:
        event: Optional[tuple[str, TaskSnapshot]] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec:
                self._global_pause = False
                rec.control.resume()
                if rec.task.status == TaskStatus.PAUSED:
                    try:
                        rec.task.transition(TaskStatus.DOWNLOADING)
                    except TransitionError:
                        pass
                    self._save_task_locked(rec)
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
            if rec.task.status == TaskStatus.QUEUED:
                rec.task.force_status(TaskStatus.CANCELLED, error="Cancelled")
                rec.task.completed_at = time.time()
                self._save_task_locked(rec)
                event = ("finished", self._snap(rec))
            elif rec.task.status in ACTIVE_STATES:
                rec.control.cancel()
                rec.task.force_status(TaskStatus.CANCELLED, error="Cancelled")
                self._save_task_locked(rec)
                event = ("updated", self._snap(rec))
        if event:
            self._emit(*event)

    def retry_task(self, task_id: str) -> None:
        # If the previous worker is still unwinding (e.g. it was just
        # cancelled), wait for it to release its part-file handles before a new
        # worker reopens them.
        thread = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec and rec.task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.COMPLETED):
                thread = rec.thread
        if thread and thread.is_alive():
            thread.join(timeout=8.0)

        event: Optional[tuple[str, TaskSnapshot]] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec and rec.task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.COMPLETED):
                rec.task.retry_count = (rec.task.retry_count or 0) + 1
                rec.task.requeue(reason="")
                rec.task.current_speed = 0.0
                rec.task.average_speed = 0.0
                rec.task.eta_seconds = None
                rec.control = TaskControl()
                self._save_task_locked(rec)
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
            if rec.task.status in ACTIVE_STATES:
                rec.removed = True
                rec.control.cancel()
                return
            snap = self._snap(rec)
            self._remove_record_locked(task_id)
            self._save_order_locked()

        self._emit("removed", snap)
        # Wait for a just-finished worker to release its file handles, then
        # remove the temporary artifacts owned by this incomplete task (never
        # the completed/final file).
        if rec.thread and rec.thread.is_alive():
            rec.thread.join(timeout=8.0)
        self._cleanup_task_files(rec.task)

    def clear_finished(self) -> None:
        self._clear_states(TERMINAL_STATES)

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

    def prepare_for_exit(self, timeout: float = 2.0) -> None:
        """Graceful app-exit: stop transfers promptly WITHOUT losing resume state.

        Sequence:
        1. Stop accepting new downloads (``_closed``).
        2. Mark every active task ``shutting_down`` and **pause** its control so
           the chunk loops stop writing at the next chunk boundary.
        3. Briefly wait for workers to reach the pause barrier.
        4. **Cancel** the controls so any thread blocked on the pause barrier
           unblocks and exits — this is what lets the process terminate quickly
           (non-daemon engine pool threads are joined at interpreter shutdown).
        5. Wait for workers to finalise, then persist once more.

        The ``shutting_down`` flag makes the worker finalize preserve the
        persisted DOWNLOADING/PAUSED state instead of marking the task
        CANCELLED, so the next launch restores it to the queue and resumes from
        the saved partial data.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            ids = list(self._tasks.keys())
            threads = [r.thread for r in self._tasks.values() if r.thread]
            for tid in ids:
                rec = self._tasks.get(tid)
                if rec and rec.task.status in ACTIVE_STATES:
                    rec.shutting_down = True
                    rec.control.pause()

        # Give workers a brief window to reach the pause barrier so the part
        # files are not left mid-write.
        deadline = time.time() + timeout
        for t in threads:
            if t and t.is_alive():
                t.join(min(0.25, max(0.0, deadline - time.time())))

        # Cancel so any thread blocked on the pause barrier unblocks and exits.
        # (Pause alone would leave the non-daemon engine pool threads blocked
        # forever and the process would hang on exit.)
        with self._lock:
            for tid in ids:
                rec = self._tasks.get(tid)
                if rec:
                    rec.control.cancel()

        # Wait for workers to finish their (shutdown-aware) finalize.
        deadline = time.time() + timeout
        for t in threads:
            if t and t.is_alive():
                t.join(max(0.0, deadline - time.time()))

        with self._lock:
            for tid in ids:
                rec = self._tasks.get(tid)
                if rec:
                    self._save_task_locked(rec)

    def close(self) -> None:
        """Flush and close the underlying database."""
        try:
            self._store.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Progress callback (called from worker threads)
    # ------------------------------------------------------------------

    def update_progress(self, task_id: str, completed: int, total: int) -> None:
        """Coalesced progress update — emits an event at most every 120 ms."""
        snap: Optional[TaskSnapshot] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec or rec.removed:
                return
            rec.task.total_size = max(0, int(total))
            rec.task.update_speed(completed)
            now = time.monotonic()
            done = rec.task.total_size > 0 and rec.task.downloaded_size >= rec.task.total_size
            if done or (now - rec.last_emit >= self._PROGRESS_EMIT_INTERVAL):
                rec.last_emit = now
                snap = self._snap(rec)
        if snap:
            self._emit("progress", snap)

    # ------------------------------------------------------------------
    # Worker management
    # ------------------------------------------------------------------

    def _start_next(self) -> None:
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            if self._closed or self._global_pause or self._scheduler_gate:
                return
            while self._active < self._max_concurrent:
                rec = next(
                    (
                        self._tasks[tid]
                        for tid in self._order
                        if tid in self._tasks
                        and self._tasks[tid].task.status == TaskStatus.QUEUED
                    ),
                    None,
                )
                if rec is None:
                    break
                try:
                    rec.task.transition(TaskStatus.ANALYZING)
                except TransitionError:
                    continue
                rec.control = TaskControl()
                rec.task.error = ""
                rec.task.completed_at = None
                self._active += 1
                t = threading.Thread(
                    target=self._worker,
                    args=(rec.id,),
                    name=f"n13-task-{rec.id}",
                    daemon=True,
                )
                rec.thread = t
                t.start()
                self._save_task_locked(rec)
                events.append(("started", self._snap(rec)))

        for ev, sn in events:
            self._emit(ev, sn)

    def _worker(self, task_id: str) -> None:
        with self._lock:
            rec = self._tasks.get(task_id)
            task = rec.task if rec else None
            control = rec.control if rec else None
            request = self._request_for(task) if task else None

        if rec is None or task is None or control is None or request is None:
            with self._lock:
                self._active = max(0, self._active - 1)
            self._start_next()
            return

        ok = False
        cancelled = False
        error = ""
        analysis = None

        # ---- ANALYZING -----------------------------------------------
        try:
            analysis = self._runner.analyze(task.id, request, control)
        except TaskCancelled:
            cancelled = True
        except Exception as exc:
            error = str(exc)
            self._logger.debug("analyze failed: %s", exc)

        # ---- STARTING → transfer -------------------------------------
        if not cancelled and not control.cancelled:
            self._apply_analysis(rec, analysis)
            if self._transition_record(rec, TaskStatus.STARTING):
                try:
                    ok = bool(
                        self._runner.download(
                            task.id,
                            request,
                            analysis,
                            lambda c, t: self.update_progress(task_id, c, t),
                            control,
                            self._task_status_cb(task_id),
                            self._task_path_cb(task_id),
                        )
                    )
                except TaskCancelled:
                    cancelled = True
                except Exception as exc:
                    error = str(exc)
                    self._logger.exception("Worker thread raised")
            else:
                cancelled = True
        else:
            cancelled = True

        # ---- Finalize ------------------------------------------------
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            self._active = max(0, self._active - 1)
            rec = self._tasks.get(task_id)
            # Guard against a retry race: if this task was re-queued and a new
            # worker was spawned while we were finishing, ``rec.control`` is no
            # longer *our* control object.  Never let the old worker clobber the
            # newer worker's state.
            if rec is not None and rec.control is not control:
                rec = None
            if rec is not None:
                t = rec.task
                if rec.shutting_down:
                    # App is exiting: keep the persisted resumable state (the
                    # task is still DOWNLOADING/PAUSED in the store so it is
                    # restored to the queue on the next launch). Do not emit a
                    # terminal event — the UI is closing.
                    self._save_task_locked(rec)
                else:
                    stopped = rec.removed or rec.control.cancelled or cancelled
                    if stopped:
                        t.force_status(TaskStatus.CANCELLED, error=t.error or error or "Cancelled")
                        t.completed_at = time.time()
                    elif ok:
                        t.force_status(TaskStatus.COMPLETED, error="")
                        if t.total_size > 0:
                            t.downloaded_size = t.total_size
                        t.completed_at = time.time()
                        # Final average speed for history (wall-time average).
                        elapsed = t.elapsed_seconds
                        if elapsed > 0.05 and t.downloaded_size > 0:
                            t.average_speed = t.downloaded_size / elapsed
                        t.current_speed = 0.0
                        t.eta_seconds = None
                        self._add_history_locked(rec, t)
                    else:
                        runner_error = getattr(self._runner, "last_error", "") or ""
                        t.force_status(
                            TaskStatus.FAILED,
                            error=t.error or error or runner_error or "Download failed",
                        )
                        t.completed_at = time.time()
                        self._add_history_locked(rec, t)

                    self._save_task_locked(rec)

                    if rec.removed:
                        # Removing an active task: the worker has now stopped,
                        # so it is safe to delete the temporary files it owns.
                        if rec.task.status != TaskStatus.COMPLETED:
                            self._cleanup_task_files(rec.task)
                        snap = self._snap(rec)
                        self._remove_record_locked(task_id)
                        events.append(("removed", snap))
                    else:
                        events.append(("finished", self._snap(rec)))

        for ev, sn in events:
            self._emit(ev, sn)

        self._start_next()

    # ------------------------------------------------------------------
    # Temporary-file cleanup
    # ------------------------------------------------------------------

    _TEMP_ARTIFACT_RE = re.compile(
        r"\.(part\d+|repart-[0-9a-f]+-\d+|tmp|merging|dlstate)$", re.IGNORECASE
    )

    def _cleanup_task_files(self, task: DownloadTask) -> List[str]:
        """Delete temporary artifacts owned by an incomplete task.

        Never touches the completed/final file.  Scoped to the exact task by
        matching against the task's resolved destination path, so it cannot
        remove files belonging to another task.

        Returns the list of paths that could not be removed (already logged).
        """
        if task.status == TaskStatus.COMPLETED:
            return []
        base = None
        if task.resolved_path:
            base = Path(task.resolved_path)
        elif task.filename or task.label:
            base = Path(task.directory) / (task.filename or task.label)
        if base is None or not base.parent.is_dir():
            return []
        try:
            match_re = re.compile(r"^" + re.escape(base.name) + r"\.(part\d+|repart-[0-9a-f]+-\d+|tmp|merging|dlstate)$", re.IGNORECASE)
        except re.error:
            return []
        failures: List[str] = []
        for entry in base.parent.iterdir():
            if not entry.is_file() or not match_re.match(entry.name):
                continue
            try:
                entry.unlink()
            except OSError as exc:
                failures.append(str(entry))
                self._logger.warning(
                    "Could not remove temporary file %s: %s", entry, exc
                )
        return failures

    # ------------------------------------------------------------------
    # State transition helpers
    # ------------------------------------------------------------------

    def _transition_record(self, rec: _TaskRecord, status: TaskStatus, error: Optional[str] = None) -> bool:
        """Validate a transition, persist, and emit an ``updated`` event."""
        snap: Optional[TaskSnapshot] = None
        with self._lock:
            if rec is None or rec.removed:
                return False
            try:
                rec.task.transition(status, error=error)
            except TransitionError:
                return False
            self._save_task_locked(rec)
            snap = self._snap(rec)
        if snap:
            self._emit("updated", snap)
        return True

    def _task_status_cb(self, task_id: str) -> Callable[[str], None]:
        def _cb(name: str) -> None:
            status = normalize_status(name)
            if status not in TaskStatus:
                return
            with self._lock:
                rec = self._tasks.get(task_id)
                if rec is None or rec.removed:
                    return
                try:
                    rec.task.transition(status)
                except TransitionError:
                    return
                self._save_task_locked(rec)
                snap = self._snap(rec)
            if snap:
                self._emit("updated", snap)
        return _cb

    def _task_path_cb(self, task_id: str) -> Callable[[str], None]:
        """Record the engine's resolved destination path so temp-file cleanup
        later knows exactly which files this task owns."""

        def _cb(path: str) -> None:
            with self._lock:
                rec = self._tasks.get(task_id)
                if rec is None or rec.removed:
                    return
                if rec.task.resolved_path != path:
                    rec.task.resolved_path = path
                    self._save_task_locked(rec)

        return _cb

    def _apply_analysis(self, rec: _TaskRecord, analysis) -> None:
        """Fold analyzer results into the task record (filename, size, etc.)."""
        if analysis is None or not getattr(analysis, "ok", False):
            return
        with self._lock:
            t = rec.task
            if getattr(analysis, "filename", None):
                t.filename = analysis.filename
            if getattr(analysis, "total_size", 0):
                t.total_size = int(analysis.total_size or 0)
            t.supports_range = bool(getattr(analysis, "supports_range", False))
            if getattr(analysis, "content_type", None):
                t.content_type = analysis.content_type
            if getattr(analysis, "server", None):
                t.server = analysis.server
            if getattr(analysis, "etag", None):
                t.etag = analysis.etag
            if getattr(analysis, "last_modified", None):
                t.last_modified = analysis.last_modified
            if t.category == "General" or not t.category:
                from core.analyzer import detect_category
                ext_map = None
                if self._config is not None:
                    ext_map = getattr(self._config, "category_extensions", None) or None
                t.category = detect_category(t.filename, t.content_type, ext_map=ext_map)
            if self._config is not None and t.connections <= 1 and t.supports_range:
                t.connections = max(1, int(getattr(self._config, "num_threads", 1) or 1))
            self._save_task_locked(rec)

    # ------------------------------------------------------------------
    # Persistence (SQLite + one-time legacy migration)
    # ------------------------------------------------------------------

    def _save_task_locked(self, rec: _TaskRecord) -> None:
        """Persist one task. Errors are swallowed (must not crash workers)."""
        try:
            self._store.save_task(rec.task.to_dict())
        except Exception:
            self._logger.warning("Could not persist task %s", rec.task.id, exc_info=True)

    def _save_order_locked(self) -> None:
        try:
            self._store.save_order(self._order)
        except Exception:
            pass

    def _restore_queue(self) -> None:
        rows = self._store.list_tasks()
        if not rows:
            # One-time migration from the legacy JSON queue file.
            self._migrate_legacy_queue()
            rows = self._store.list_tasks()

        with self._lock:
            for row in rows:
                try:
                    task = DownloadTask.from_dict(row)
                except Exception:
                    continue
                if not task.id or not task.url or not task.directory:
                    continue
                if task.status == TaskStatus.COMPLETED:
                    continue
                # Interrupted mid-flight tasks go back to the queue.
                if task.status in ACTIVE_STATES:
                    task.force_status(TaskStatus.QUEUED)
                    task.error = "Restored after restart"
                self._tasks[task.id] = _TaskRecord(task=task)
                self._order.append(task.id)

            # Apply persisted explicit ordering (move up/down) when available.
            saved_order = self._store.load_order()
            if saved_order:
                by_id = {t: i for i, t in enumerate(self._order)}
                ordered = [t for t in saved_order if t in by_id]
                for tid in self._order:
                    if tid not in ordered:
                        ordered.append(tid)
                self._order = ordered
            else:
                # Stable default: priority (higher first) then creation time.
                self._order.sort(
                    key=lambda tid: (self._tasks[tid].task.priority, self._tasks[tid].task.created_at)
                )
            self._save_order_locked()

    def _migrate_legacy_queue(self) -> None:
        if not self._queue_file.exists():
            return
        items = self._load_json(self._queue_file, [])
        if isinstance(items, list):
            self._store.import_legacy_queue(items)
        try:
            self._queue_file.rename(self._queue_file.with_suffix(".json.imported"))
        except OSError:
            pass

    def _load_history(self) -> List[Dict[str, Any]]:
        history = self._store.list_history(500)
        if not history and self._history_file.exists():
            entries = self._load_json(self._history_file, [])
            if isinstance(entries, list):
                self._store.import_legacy_history(entries)
                history = self._store.list_history(500)
        return history

    def _add_history_locked(self, rec: _TaskRecord, task: DownloadTask) -> None:
        entry = {
            "task_id": task.id,
            "url": task.url,
            "directory": task.directory,
            "name": task.filename or task.label or name_from_url(task.url),
            "category": task.category or "General",
            "size_bytes": int(task.downloaded_size or task.total_size or 0),
            "status": task.status.value,
            "duration": round(task.elapsed_seconds, 1),
            "avg_speed": round(task.average_speed, 1),
            "finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            self._store.add_history(entry)
        except Exception:
            self._logger.warning("Could not write history", exc_info=True)
        self.history = self._store.list_history(500)

    def clear_history(self) -> None:
        with self._lock:
            self.history = []
            try:
                self._store.clear_history()
            except Exception:
                pass

    def remove_history(self, task_id: str) -> None:
        """Remove a single history entry by task id."""
        with self._lock:
            try:
                self._store.delete_history(task_id)
            except Exception:
                pass
            self.history = self._store.list_history(500)

    def clear_completed_failed_history(self) -> None:
        with self._lock:
            try:
                self._store.clear_finished_history()
            except Exception:
                pass
            self.history = self._store.list_history(500)

    # ------------------------------------------------------------------
    # Crash recovery
    # ------------------------------------------------------------------

    def recover_unfinished(self) -> int:
        """Validate restored tasks and requeue resumable ones.

        Runs once at startup.  Returns the number of tasks re-queued for
        download.  Tasks whose destination directory no longer exists are kept
        queued with a warning (the engine re-creates the folder).  A file that
        was fully merged but whose task was interrupted before it could be
        marked complete (crash between merge and rename) is detected here and
        recorded as completed.

        Segment-level validation (part file sizes, path containment) is done by
        the engine itself when a restored task starts — duplicating that logic
        here would create a second, divergent download engine.
        """
        requeued = 0
        completed_ids: List[str] = []
        with self._lock:
            for tid in list(self._order):
                rec = self._tasks.get(tid)
                if not rec:
                    continue
                task = rec.task
                if task.status != TaskStatus.QUEUED:
                    continue
                directory = Path(task.directory)
                if not directory.is_dir():
                    task.error = "Destination folder not found — will be re-created"
                    self._save_task_locked(rec)
                    requeued += 1
                    continue
                # Crash between merge and rename: the file exists and matches
                # the expected size, but the task never reached COMPLETED.
                if task.filename:
                    final = directory / task.filename
                    try:
                        if (
                            task.total_size > 0
                            and final.is_file()
                            and final.stat().st_size == task.total_size
                        ):
                            task.downloaded_size = task.total_size
                            task.force_status(TaskStatus.COMPLETED, error="")
                            task.completed_at = time.time()
                            self._save_task_locked(rec)
                            self._add_history_locked(rec, task)
                            completed_ids.append(tid)
                            continue
                    except OSError:
                        pass
                task.error = "Restored after restart"
                self._save_task_locked(rec)
                requeued += 1
        if completed_ids:
            for tid in completed_ids:
                self.remove_task(tid)
        return requeued

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _remove_record_locked(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
        try:
            self._order.remove(task_id)
        except ValueError:
            pass
        try:
            self._store.delete_task(task_id)
        except Exception:
            pass

    @staticmethod
    def _write_json_atomic(path: Path, data: Any) -> None:
        """Atomically write JSON (kept for backward compatibility)."""
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
