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
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Set

from rich.console import Console

from core import artifacts
from core.control import TaskCancelled, TaskControl
from core.db import DEFAULT_PROJECT_ID
from core.store import TaskStore
from core.task import (
    ACTIVE_STATES,
    INTERRUPTED_STATES as _INTERRUPTED_STATES,
    TERMINAL_STATES,
    DownloadTask,
    TaskStatus,
    TransitionError,
    is_terminal,
    normalize_status,
)
from core.urls import same_resource as _same_resource
from core.urls import url_filename
# Policy only — never the service.  The queue must be able to decide whether a
# project lets a task start without importing SQLite, and without a cycle back
# into this module.  See docs/PROJECTS.md §2.
from projects.admission import ProjectGate
from ui.queue_projects import ProjectAdmissionMixin

# Re-export for callers that reference the legacy name (ui.api etc.).
TaskState = TaskStatus

# Engine-reported phases that are *not* task states.  The download engine
# announces CONNECTING before it has a body byte and DOWNLOADING on the first
# byte; the queue keeps the task in STARTING for the former, so a server that
# accepts the connection and then says nothing cannot look like a running
# transfer.  Listing them here keeps that intent explicit instead of relying on
# a transition that fails and is silently swallowed.
_ENGINE_PHASE_STATUSES = frozenset({"CONNECTING", "PROBING", "WAITING"})


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

# Re-exported so existing callers (`from ui.common import human_size`) keep
# working.  The implementation lives in the core layer next to the other
# formatters, which is also where `core/store.py` now gets it from — it used to
# carry its own private duplicate.
from core.utils import human_size  # noqa: E402  (kept at its historical home)


def format_eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0 or seconds > 359_999:
        return "--:--"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _sanitize_filename(name: str) -> str:
    """Clean a user-supplied filename; returns "" when it is unusable.

    Only the final path component is kept, so a rename can never traverse out
    of the task's directory.  The character cleanup is delegated to the
    engine's own :func:`core.utils.sanitize_filename` so a renamed task and a
    freshly probed task produce identically shaped names.
    """
    raw = (name or "").strip().replace("\\", "/").split("/")[-1].strip()
    if not raw or raw in {".", ".."} or not re.search(r"[A-Za-z0-9]", raw):
        return ""
    from core.utils import sanitize_filename as _engine_sanitize

    return _engine_sanitize(raw)


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return default


def name_from_url(url: str, label: str = "") -> str:
    """Best-effort file name from a URL (used when no server filename yet).

    Delegates to :func:`core.urls.url_filename` so the name shown in the UI and
    the name the engine saves are produced by one implementation — including
    the ``download.php?file=setup.exe`` shape, where the filename only exists
    in the query string.
    """
    if label:
        return label
    return url_filename(url) or (url or "")


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
    connection_mode: str = ""      # "" | "smart" | "manual" (rule override)
    num_threads: int = 0           # used when connection_mode == "manual"
    # The project this download belongs to.  Defaults to the Default project so
    # an add call from a caller that predates the project layer still lands
    # somewhere visible instead of becoming an orphan row.
    project_id: str = DEFAULT_PROJECT_ID
    # Task-scoped probe hand-off: an already-computed Analysis for THIS url
    # (produced by the UI's probe step).  Never trusted blindly — the runner
    # re-validates URL match, freshness and SSRF before using it, and falls
    # back to its own probe otherwise.
    probe_analysis: Any = None

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
    smart_status: str = ""
    connection_mode: str = ""
    num_threads: int = 0
    # Position in the manager's queue order (0 = next up).  ``-1`` means the
    # task is not in the order list at all.  Purely informational: it lets the
    # UI offer an honest "queue order" view and Move up/down controls instead
    # of reordering something the user cannot see.
    queue_index: int = -1
    # The position this task will ACTUALLY start in (1 = next up), or ``-1``
    # when it is not waiting.  This is ``queue_index`` re-scored by priority,
    # i.e. the same ``(priority, manual position)`` rule the scheduler uses, so
    # it is the only number the UI may label "queue position".  The two differ
    # exactly when priority is doing something — see docs/QUEUE.md §2.
    queue_position: int = -1
    # The owning project.  Resolved (never empty) so a renderer can group by it
    # without having to know about the Default fallback.
    project_id: str = DEFAULT_PROJECT_ID

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
            "project_id": self.project_id,
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
            "smart_status": self.smart_status,
            "connection_mode": self.connection_mode,
            "num_threads": self.num_threads,
            "speed_limit_bps": self.request.speed_limit_bps,
            "queue_index": self.queue_index,
            "queue_position": self.queue_position,
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
        smart_callback: Optional[Callable[[str], None]] = None,
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
    # Probe hand-off carried from the original DownloadRequest into the
    # worker thread (task-scoped; never persisted).
    probe_analysis: Any = None

    @property
    def id(self) -> str:
        return self.task.id


def _simulate_slot_starts(
    durations: Sequence[Optional[float]],
    capacity: int,
) -> List[Optional[float]]:
    """When each job in *durations* starts, given *capacity* parallel slots.

    A plain list-scheduling simulation: every job takes the slot that frees
    earliest, so the jobs ahead of you genuinely decide when you start instead
    of us assuming they run one after another.  ``durations`` is ordered —
    running downloads first (their own remaining time), then queued ones.

    Every slot starts free at ``t=0``: the running downloads are already the
    first entries of ``durations``, so they occupy their slots by being
    scheduled, and must not *also* be pre-marked busy (that would double-count
    them and stall the whole queue).

    ``None`` means "unknown duration"; the job still occupies its slot, and any
    job that cannot be placed behind it inherits ``None`` (an honest "cannot
    estimate") rather than a fabricated number.
    """
    capacity = max(1, int(capacity))
    # Slot free-at times; all slots are free when the simulation begins.
    slot_free: List[Optional[float]] = [0.0] * capacity

    starts: List[Optional[float]] = []
    for duration in durations:
        # Pick the slot that frees first; None (unknown) sorts last.
        known = [(t, i) for i, t in enumerate(slot_free) if t is not None]
        if known:
            start, idx = min(known)
            if duration is None:
                slot_free[idx] = None
            else:
                slot_free[idx] = start + max(0.0, float(duration))
            starts.append(float(start))
        else:
            # Every slot is held by a job of unknown length.
            starts.append(None)
    return starts


# ---------------------------------------------------------------------------
# TaskManager
# ---------------------------------------------------------------------------

class TaskManager(ProjectAdmissionMixin):
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
        store: Optional[TaskStore] = None,
        project_gates: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._runner = runner
        self._config = config
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        # The store may be injected so the application can build the project
        # layer on the *same* connection first and hand over its gate snapshot
        # below.  Without that ordering a project the user paused would still get
        # its restored downloads started here, before the gates arrived.
        self._store = store if store is not None else TaskStore(self._storage_dir / "downloads.db")
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
        # Live worker threads per project.  Maintained in exactly the same three
        # places as ``_active`` (see ``_acquire_slot`` / ``_release_slot``) so the
        # project limit can never disagree with the global one about what
        # "currently running" means.
        self._active_by_project: Dict[str, int] = {}
        # Per-project admission snapshot, pushed by the API layer.  Absent
        # project => open and unlimited, so a project row that has not loaded yet
        # can never block the whole queue.
        self._project_gates: Dict[str, ProjectGate] = {
            str(pid): ProjectGate.coerce(gate)
            for pid, gate in (project_gates or {}).items()
        }
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

    @property
    def store(self) -> TaskStore:
        """The task/history repository this manager persists through.

        Exposed so the application can build the project layer on the same
        connection instead of opening a second one to the same file.
        """
        return self._store

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
            connection_mode=task.connection_mode,
            num_threads=task.num_threads,
            project_id=task.project_id or DEFAULT_PROJECT_ID,
        )

    def _queue_index(self, task_id: str) -> int:
        """This task's position in the queue order, or -1 if it is not queued.

        The order list is short (one entry per known task), so a linear scan
        is cheaper than keeping a parallel index map in sync.
        """
        try:
            return self._order.index(task_id)
        except ValueError:
            return -1

    @staticmethod
    def _priority_of(rec: _TaskRecord) -> int:
        """A task's priority as an int, defaulting to 5 when it is nonsense.

        The single definition of "the priority used for selection" — the
        scheduler, the plan and the position report all read it, so they can
        never disagree about a malformed value.
        """
        try:
            return int(rec.task.priority)
        except (TypeError, ValueError):
            return 5

    def _queued_in_start_order_locked(self) -> List[tuple]:
        """Waiting tasks as ``(priority, manual_index, record)``, in the order
        they will actually start.

        **The** definition of effective start order.  ``queue_plan`` and
        ``queue_positions`` both read it, so a position shown in the UI can
        never disagree with the plan shown next to it.  Callers must hold
        ``self._lock``.
        """
        queued: List[tuple] = []
        for index, tid in enumerate(self._order):
            rec = self._tasks.get(tid)
            if rec is None or rec.task.status != TaskStatus.QUEUED:
                continue
            queued.append((self._priority_of(rec), index, rec))
        # Strictly by (priority, manual position) — the same key
        # `_next_queued_locked` minimises, so the list head is what starts next
        # and ties keep the earlier manual position.
        queued.sort(key=lambda item: (item[0], item[1]))
        return queued

    def _queue_position_locked(self, rec: _TaskRecord) -> int:
        """Effective 1-based start position of one task, or -1 if not waiting.

        Counts the waiting tasks that outrank this one instead of sorting the
        whole queue, so filling a single snapshot costs O(n) — the same as the
        manual ``queue_index`` it sits beside, and cheap enough to call for
        every event rather than only in bulk.
        """
        if rec.task.status != TaskStatus.QUEUED:
            return -1
        mine = (self._priority_of(rec), self._queue_index(rec.id))
        ahead = 0
        for index, tid in enumerate(self._order):
            other = self._tasks.get(tid)
            if other is None or other.id == rec.id:
                continue
            if other.task.status != TaskStatus.QUEUED:
                continue
            if (self._priority_of(other), index) < mine:
                ahead += 1
        return ahead + 1

    def queue_positions(self) -> Dict[str, int]:
        """Effective 1-based start position of every waiting task.

        This is the order downloads will really start in, which is **not** the
        manual order once priorities differ.  Tasks that are not waiting are
        absent.
        """
        with self._lock:
            return {
                rec.id: offset + 1
                for offset, (_pri, _index, rec) in enumerate(
                    self._queued_in_start_order_locked()
                )
            }

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
            smart_status=t.smart_status,
            connection_mode=t.connection_mode,
            num_threads=t.num_threads,
            queue_index=self._queue_index(t.id),
            queue_position=self._queue_position_locked(rec),
            project_id=t.project_id or DEFAULT_PROJECT_ID,
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
        _save_order: bool = True,
    ) -> str:
        """Add one download.  ``_save_order`` is an internal bulk-add hook:
        ``add_many`` turns it off and persists the order once at the end
        instead of rewriting the whole queue order per item."""
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            if not allow_duplicate:
                for tid in self._order:
                    rec = self._tasks.get(tid)
                    if (
                        rec is not None
                        and _same_resource(rec.task.url, request.url)
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
                connection_mode=request.connection_mode,
                num_threads=request.num_threads,
                project_id=request.project_id or DEFAULT_PROJECT_ID,
            )
            task_id = task.id
            rec = _TaskRecord(task=task, probe_analysis=request.probe_analysis)
            self._tasks[task_id] = rec
            self._order.append(task_id)
            self._save_task_locked(rec)
            # Persist the new position immediately, otherwise `queue_order`
            # drifts from the live order until some unrelated edit saves it.
            if _save_order:
                self._save_order_locked()
            events.append(("added", self._snap(rec)))

        for ev, sn in events:
            self._emit(ev, sn)
        if autostart:
            self._start_next()
        return task_id

    def check_duplicate(
        self,
        url: str,
        directory: str,
        filename: str = "",
    ) -> Dict[str, Any]:
        """Detect a potential duplicate for a download about to be added.

        Detection levels (cheap, metadata-first — never downloads a file):
        1. Same URL already queued/downloading/paused in this session.
        2. Same URL present in History.
        3. A file already exists at the destination path.

        URL comparison is by :func:`core.urls.same_resource`, which folds away
        only what cannot change the bytes served (scheme/host case, the default
        port, the fragment).  Query strings are compared verbatim, so two
        different signed links are never reported as the same download.

        Returns a plain dict consumed by the UI.  ``reason`` is ``""`` when no
        conflict is found.
        """
        url = (url or "").strip()
        result: Dict[str, Any] = {
            "has_active": False,
            "active_task_id": "",
            "in_history": False,
            "history_count": 0,
            "file_exists": False,
            "file_path": "",
            "reason": "",
        }
        if not url:
            return result

        # Level 1 — same URL active in this session (queued/downloading/paused).
        with self._lock:
            for tid in self._order:
                rec = self._tasks.get(tid)
                if (
                    rec is not None
                    and _same_resource(rec.task.url, url)
                    and not is_terminal(rec.task.status)
                ):
                    result["has_active"] = True
                    result["active_task_id"] = rec.id
                    result["reason"] = "same_url"
                    return result

        # Level 2 — same URL in history.
        for h in self.history:
            if _same_resource(h.get("url") or "", url):
                result["in_history"] = True
                result["history_count"] += 1
                result["reason"] = result["reason"] or "in_history"

        # Level 3 — file already exists at the destination.
        if filename:
            try:
                p = Path(directory or "") / filename
                if p.is_file():
                    result["file_exists"] = True
                    result["file_path"] = str(p)
                    result["reason"] = result["reason"] or "file_exists"
            except OSError:
                pass

        return result

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
        # Suppress the per-item order write: each one rewrites the entire
        # queue_order table, so a large batch would be quadratic.  One save at
        # the end leaves the persisted order identical.
        ids = [
            self.add(r, autostart=autostart, allow_duplicate=allow_duplicate, _save_order=False)
            for r in requests
        ]
        with self._lock:
            self._save_order_locked()
        return ids

    def start_all(self) -> None:
        """Fill every free concurrency slot from the waiting queue.

        The legacy name overstates it: this starts *whatever fits right now*,
        not every task.  It honours the queue gate and ``max_concurrent``
        exactly like any other start, so it is also what a batch add calls
        after queueing its requests.
        """
        self._start_next()

    def start_task(self, task_id: str) -> None:
        """Start a specific QUEUED task now (no-op for other states).

        "Start" is an explicit *run this now* request, so it also **opens the
        queue gate**.  A Start button that silently does nothing because the
        queue happens to be paused is exactly the hidden state this queue is
        not allowed to have; the gate is rendered in the UI, so the side effect
        is visible and one click from being undone.

        The scheduler gate is different — a window the user configured is a
        constraint, not a pause — so it is still respected, as is
        ``max_concurrent``: with every slot busy the task simply moves to the
        front of the queue and starts when one frees.

        The same reasoning applies to a *project* pause or schedule window.  They
        are the user's own rules about that project, so an explicit Start does not
        override them; the task moves to the front and starts as soon as the
        project allows it.  Resume the project to lift the block.
        """
        events: List[tuple[str, TaskSnapshot]] = []
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
            if not (self._closed or self._scheduler_gate):
                if not self._project_blocks_locked(rec):
                    self._global_pause = False
                    if self._start_rec_locked(rec):
                        events.append(("started", self._snap(rec)))
        for ev, sn in events:
            self._emit(ev, sn)
        self._start_next()

    @property
    def queue_paused(self) -> bool:
        """Whether the queue gate is closed, i.e. no NEW download will start.

        Deliberately distinct from a task being PAUSED: the gate blocks the
        queue, a paused task stops one download, and neither implies the other.
        See docs/QUEUE.md §1.
        """
        with self._lock:
            return self._global_pause

    def pause_queue(self) -> None:
        """Close the queue gate: start nothing new, leave running downloads be.

        Unlike :meth:`pause_all` this does not touch any task, so the
        downloads already in flight keep going — it only stops the queue from
        feeding the next one in.
        """
        with self._lock:
            self._global_pause = True

    def resume_queue(self) -> None:
        """Open the queue gate and start whatever now fits."""
        with self._lock:
            self._global_pause = False
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

    def set_task_speed_limit(self, task_id: str, bps: int) -> None:
        """Set a per-download bandwidth cap in bytes/second (0 = unlimited).

        The cap is stored on the task and handed to the engine when the
        download runs, so it survives restarts and coexists with the global
        ``max_speed_bps`` limit.
        """
        event: Optional[tuple[str, TaskSnapshot]] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec:
                rec.task.speed_limit_bps = max(0, int(bps or 0))
                self._save_task_locked(rec)
                event = ("updated", self._snap(rec))
        if event:
            self._emit(*event)

    def rename_task(self, task_id: str, new_name: str) -> Dict[str, Any]:
        """Rename a download's target file.

        Refused while the task is actively transferring, because the engine
        holds the destination path open.  For a completed download the file on
        disk is renamed as well so the record and the filesystem stay in sync;
        for every other state only the queued name changes.

        Returns ``{"ok": bool, "error": str, "name": str, "path": str}``.
        """
        clean = _sanitize_filename(new_name)
        if not clean:
            return {"ok": False, "error": "invalid_name", "name": "", "path": ""}

        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None:
                return {"ok": False, "error": "not_found", "name": "", "path": ""}

            task = rec.task
            if task.status in ACTIVE_STATES:
                return {"ok": False, "error": "task_active", "name": task.filename, "path": ""}

            old_name = task.filename or task.label or ""
            directory = task.directory or ""
            moved_from = ""
            moved_to = ""

            if task.status == TaskStatus.COMPLETED and directory:
                src = Path(directory) / old_name if old_name else None
                dst = Path(directory) / clean
                if src is not None and src.name != clean and src.exists():
                    if dst.exists():
                        return {"ok": False, "error": "target_exists", "name": old_name, "path": str(dst)}
                    try:
                        os.replace(str(src), str(dst))
                    except OSError as exc:
                        return {"ok": False, "error": f"rename_failed: {exc}", "name": old_name, "path": ""}
                    moved_from, moved_to = str(src), str(dst)

            task.filename = clean
            task.label = clean
            # A completed task's resolved path must follow the rename so
            # "Open file" / "Open folder" keep working.
            if moved_to:
                task.resolved_path = moved_to
            self._save_task_locked(rec)
            snap = self._snap(rec)

        self._emit("updated", snap)
        return {"ok": True, "error": "", "name": clean, "path": moved_to}

    def move_task(self, task_id: str, delta: int) -> None:
        """Move a task up (-1) or down (+1) in the queue order.

        Every task whose position shifted emits ``updated`` so a queue-order
        view in the UI refreshes immediately.  Nothing is started or stopped
        here: reordering only decides who goes next once a slot frees up,
        which is exactly how ``set_priority`` behaves.
        """
        shifted: List[TaskSnapshot] = []
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
            lo, hi = (idx, new_idx) if idx < new_idx else (new_idx, idx)
            for tid in self._order[lo:hi + 1]:
                rec = self._tasks.get(tid)
                if rec is not None:
                    shifted.append(self._snap(rec))
        for snap in shifted:
            self._emit("updated", snap)

    # ── Absolute ordering (drag & drop, move to top/bottom) ──────────
    #
    # `move_task` above stays the relative ±1 primitive; everything here is
    # built on one absolute primitive so drag & drop and the keyboard
    # shortcuts share a single, well-tested code path.

    def reorder_tasks(
        self,
        task_ids: Sequence[str],
        position: Optional[int] = None,
    ) -> None:
        """Move *task_ids* as one contiguous block, keeping the given order.

        * With ``position=None`` the block collapses onto the slot of its
          *topmost* member, keeping the order the caller listed.  This is the
          "gather these together without moving them anywhere" case, and it
          guarantees the block never jumps down past tasks the caller did not
          select.
        * With ``position=N`` the block's first task ends up at index N of the
          resulting queue (0 = next up), which is the ``newIndex`` a drag &
          drop handler naturally reports.

        Tasks that are unknown, or that are not in the queue order, are
        ignored rather than raising, so a stale UI selection can never wedge
        the queue.  Ordering never starts or stops anything: it only decides
        who goes next once a slot frees.
        """
        wanted = [t for t in dict.fromkeys(task_ids or []) if t]
        if not wanted:
            return
        shifted: List[TaskSnapshot] = []
        with self._lock:
            present = [t for t in wanted if t in self._order]
            if not present:
                return
            if position is None:
                target = min(self._order.index(t) for t in present)
            else:
                try:
                    target = int(position)
                except (TypeError, ValueError):
                    target = 0
                target = max(0, min(len(self._order), target))
            moving = set(present)
            rest = [t for t in self._order if t not in moving]
            # `target` is the block's FINAL index, which is exactly an index
            # into `rest` — the moving tasks are already gone from `rest`, and
            # the block is spliced back in at that spot.  No correction is
            # needed (and applying one would shift the block by the number of
            # moved items that happened to sit before the target).
            insert_at = max(0, min(len(rest), target))
            new_order = rest[:insert_at] + present + rest[insert_at:]
            if new_order == self._order:
                return
            self._order = new_order
            self._save_order_locked()
            shifted = [self._snap(self._tasks[t]) for t in self._order if t in self._tasks]
        for snap in shifted:
            self._emit("updated", snap)

    def move_task_to(self, task_id: str, position: int) -> None:
        """Move one task so it ends up at absolute queue index *position*."""
        self.reorder_tasks([task_id], position)

    def move_to_top(self, task_id: str) -> None:
        """Move one task to the front of the queue."""
        self.reorder_tasks([task_id], 0)

    def move_to_bottom(self, task_id: str) -> None:
        """Move one task to the end of the queue."""
        with self._lock:
            if task_id not in self._order:
                return
            end = len(self._order)
        self.reorder_tasks([task_id], end)

    def queue_plan(self) -> List[Dict[str, Any]]:
        """Effective execution plan for the waiting queue.

        Returns one entry per QUEUED task, ordered by the order it will
        actually start in — the same ``(priority, manual position)`` rule
        ``_start_next`` uses, which is *not* the same as raw queue position
        once priorities differ.

        ``estimated_start_seconds`` is simulated, not guessed: running
        downloads are scheduled by their own remaining time, queued tasks by
        remaining bytes at the throughput actually being achieved per slot.
        When there is nothing running and no measured throughput, the estimate
        is ``None`` rather than a fabricated number.
        """
        with self._lock:
            # Same ordering helper the per-task `queue_position` uses, so the
            # plan and the position shown in the list cannot drift apart.
            queued = self._queued_in_start_order_locked()

            active: List[_TaskRecord] = [
                rec for tid in self._order
                if (rec := self._tasks.get(tid)) is not None
                and rec.task.status in ACTIVE_STATES
            ]

            # Work that must clear before each queued task can start.
            active_durations: List[Optional[float]] = []
            for rec in active:
                t = rec.task
                remaining = max(0, int(t.total_size) - int(t.downloaded_size))
                eta = t.eta_seconds
                if eta is not None and eta >= 0:
                    active_durations.append(float(eta))
                elif t.current_speed and t.current_speed > 0 and remaining:
                    active_durations.append(remaining / float(t.current_speed))
                else:
                    # Genuinely unknown.  It still holds its slot, but claiming
                    # "0 seconds" would make the task behind it report
                    # "starts immediately" — a fabricated number.  None keeps
                    # the queue honest and everything behind it unestimated.
                    active_durations.append(None)

            total_speed = sum(float(r.task.current_speed or 0) for r in active)
            slots_busy = len(active)
            per_slot = (total_speed / slots_busy) if slots_busy and total_speed > 0 else 0.0

            # Running downloads come first: they already hold their slots, so
            # the simulation places them before any queued task.
            durations: List[Optional[float]] = list(active_durations)
            for _pri, _index, rec in queued:
                t = rec.task
                remaining = max(0, int(t.total_size) - int(t.downloaded_size))
                if per_slot > 0 and remaining > 0:
                    durations.append(remaining / per_slot)
                else:
                    durations.append(None)

            starts = _simulate_slot_starts(durations, self._max_concurrent)

            plan: List[Dict[str, Any]] = []
            for offset, (pri, index, rec) in enumerate(queued):
                t = rec.task
                start = starts[len(active_durations) + offset]
                plan.append({
                    "id": t.id,
                    # Same fallback chain as history entries: a queued task has
                    # no probed filename yet, so the URL basename is what the
                    # user actually recognises in the queue list.
                    "name": t.filename or t.label or name_from_url(t.url),
                    "state": t.status.value,
                    "priority": pri,
                    "position": offset + 1,          # 1-based effective start order
                    "manual_index": index,           # raw position in _order
                    "total": int(t.total_size or 0),
                    "completed": int(t.downloaded_size or 0),
                    "remaining": max(0, int(t.total_size) - int(t.downloaded_size)),
                    "category": t.category or "General",
                    "speed_limit_bps": int(t.speed_limit_bps or 0),
                    "estimated_start_seconds": start,
                    "expected_wait_seconds": start,
                    "starts_immediately": start is not None and start <= 0.0,
                })
            return plan

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
        """Pause **everything**: close the queue gate AND stop every active task.

        This is the "stop the whole thing" button.  Use :meth:`pause_queue` to
        stop new downloads starting while letting the current ones finish.
        """
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

    @staticmethod
    def _has_live_worker(rec: _TaskRecord) -> bool:
        """Whether this record still owns a running worker thread."""
        return rec.thread is not None and rec.thread.is_alive()

    def _resume_paused_locked(self, rec: _TaskRecord) -> bool:
        """Resume one PAUSED task.  Callers must hold ``self._lock``.

        Two genuinely different situations hide behind "Paused", and they need
        different handling:

        * **Live pause** — the worker that paused is still parked at its pause
          barrier, so releasing the barrier continues the transfer exactly where
          it stopped.
        * **Restored pause** — the process was restarted, so there is no worker
          and no occupied slot.  Transitioning straight to DOWNLOADING would
          leave the task claiming to download forever with nothing doing the
          work, so it goes back to QUEUED and the scheduler starts it like any
          other waiting download.

        Returns True when the record changed.
        """
        rec.control.resume()
        if self._has_live_worker(rec):
            try:
                rec.task.transition(TaskStatus.DOWNLOADING)
            except TransitionError:
                return False
            self._save_task_locked(rec)
            return True
        try:
            rec.task.transition(TaskStatus.QUEUED)
        except TransitionError:
            return False
        rec.task.error = ""
        self._save_task_locked(rec)
        return True

    def resume_all(self) -> None:
        """Resume **everything**: open the queue gate AND resume every pause."""
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            self._global_pause = False
            for tid in self._order:
                rec = self._tasks.get(tid)
                if not rec or rec.task.status != TaskStatus.PAUSED:
                    continue
                if self._resume_paused_locked(rec):
                    events.append(("updated", self._snap(rec)))
        for ev, sn in events:
            self._emit(ev, sn)
        self._start_next()

    def resume_task(self, task_id: str) -> None:
        """Resume one paused task.

        **Never touches the queue gate.**  Resuming a single row must not
        quietly start every other waiting download — that is the ambiguity this
        method used to have, and it is why ``_global_pause`` is left exactly as
        it was.  Use :meth:`resume_queue` or :meth:`resume_all` to open the
        gate deliberately.
        """
        event: Optional[tuple[str, TaskSnapshot]] = None
        with self._lock:
            rec = self._tasks.get(task_id)
            if (
                rec
                and rec.task.status == TaskStatus.PAUSED
                and self._resume_paused_locked(rec)
            ):
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
    #
    # The project-aware half of worker management — slot accounting, the
    # admission check and the gate snapshot — lives in
    # ``ui/queue_projects.py::ProjectAdmissionMixin``, so this file keeps
    # owning the queue and that file owns the project integration.

    def _next_queued_locked(self, skip: Optional[set] = None) -> Optional[_TaskRecord]:
        """The QUEUED task that should start next, or ``None``.

        Selection key is ``(priority, manual position)``: a lower priority
        number wins, and the user's explicit ordering (``_order`` — driven by
        Move up/down, drag & drop and ``move_task_to``) breaks ties.

        ``_order`` itself is deliberately **never** re-sorted here.  Keeping the
        manual order intact is what makes priority and hand-ordering coexist:
        raising one task's priority does not scramble everything the user
        arranged by hand.
        """
        best: Optional[_TaskRecord] = None
        best_key: Optional[tuple] = None
        for index, tid in enumerate(self._order):
            if skip and tid in skip:
                continue
            rec = self._tasks.get(tid)
            if rec is None or rec.task.status != TaskStatus.QUEUED:
                continue
            try:
                pri = int(rec.task.priority)
            except (TypeError, ValueError):
                pri = 5
            key = (pri, index)
            # Strictly-less keeps the FIRST task in manual order for a tie.
            if best_key is None or key < best_key:
                best, best_key = rec, key
        return best

    def _start_blocked(self) -> bool:
        """Whether new downloads are currently forbidden from starting.

        One predicate for all three gates, so a future addition cannot be
        honoured in one branch and forgotten in another.  Callers must hold
        ``self._lock``.
        """
        return self._closed or self._global_pause or self._scheduler_gate

    def _start_rec_locked(self, rec: _TaskRecord) -> bool:
        """Spawn the worker for one QUEUED task.  Callers must hold the lock.

        The **only** place a download worker is created.  Returns False —
        without raising — when every concurrency slot is busy or the task
        cannot legally move to ANALYZING, so callers can simply move on.
        """
        if self._active >= self._max_concurrent:
            return False
        try:
            rec.task.transition(TaskStatus.ANALYZING)
        except TransitionError:
            return False
        rec.control = TaskControl()
        rec.task.error = ""
        rec.task.completed_at = None
        self._acquire_slot(rec.task.project_id)
        t = threading.Thread(
            target=self._worker,
            args=(rec.id,),
            name=f"n13-task-{rec.id}",
            daemon=True,
        )
        rec.thread = t
        t.start()
        self._save_task_locked(rec)
        return True

    def _start_next(self) -> None:
        events: List[tuple[str, TaskSnapshot]] = []
        with self._lock:
            if self._start_blocked():
                return
            # Tasks that could not be started are skipped for the rest of this
            # pass.  Without this the loop would re-select the same task forever
            # while holding the lock, hanging every other thread.
            #
            # Skipping (rather than stopping at) a blocked task is also what stops
            # a project from starving the ones behind it: a project whose window
            # is closed sits at the head of the queue and every other project
            # still gets its turn.
            attempted: set = set()
            while self._active < self._max_concurrent:
                rec = self._next_queued_locked(skip=attempted)
                if rec is None:
                    break
                if not self._admission_locked(rec).allowed:
                    attempted.add(rec.id)
                    continue
                if not self._start_rec_locked(rec):
                    attempted.add(rec.id)
                    continue
                events.append(("started", self._snap(rec)))

        for ev, sn in events:
            self._emit(ev, sn)

    def _worker(self, task_id: str) -> None:
        with self._lock:
            rec = self._tasks.get(task_id)
            task = rec.task if rec else None
            control = rec.control if rec else None
            request = self._request_for(task) if task else None
            probe_handoff = rec.probe_analysis if rec else None
            # Captured now because ``rec`` may be gone by the time the slot is
            # released, and the project counter must be decremented for the same
            # project it was incremented for.
            slot_project = (task.project_id if task else "") or DEFAULT_PROJECT_ID

        # Attach the task-scoped probe hand-off so the runner can reuse the
        # UI's probe result instead of probing the same URL a second time.
        if request is not None and probe_handoff is not None:
            from dataclasses import replace

            request = replace(request, probe_analysis=probe_handoff)

        if rec is None or task is None or control is None or request is None:
            with self._lock:
                self._release_slot(slot_project)
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
                            self._task_smart_cb(task_id),
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
            self._release_slot(slot_project)
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
                        # This path has no other order write, so persist here.
                        self._remove_record_locked(task_id, save_order=True)
                        events.append(("removed", snap))
                    else:
                        events.append(("finished", self._snap(rec)))

        for ev, sn in events:
            self._emit(ev, sn)

        self._start_next()

    # ------------------------------------------------------------------
    # Temporary-file cleanup
    # ------------------------------------------------------------------

    def _cleanup_task_files(self, task: DownloadTask) -> List[str]:
        """Delete temporary artifacts owned by an incomplete task.

        Never touches the completed/final file.  Scoped to the exact task by
        matching against the task's resolved destination path, so it cannot
        remove files belonging to another task.

        The suffix list lives in :mod:`core.artifacts` because
        ``projects/service.py`` needs the same definition when a user deletes a
        project together with its downloads; two copies would drift and one of
        them would start leaving orphans behind.

        Returns the list of paths that could not be removed (already logged).
        """
        if task.status == TaskStatus.COMPLETED:
            return []
        base = artifacts.task_base_path(
            task.resolved_path, task.directory, task.filename, task.label
        )
        if base is None or not base.parent.is_dir():
            return []
        failures: List[str] = []
        for entry in artifacts.iter_artifacts(base):
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
            # Informational phases from the engine that are deliberately NOT
            # TaskStatus values.  The task stays in STARTING until the first
            # body byte arrives, so "connecting" must not be coerced into a
            # bogus transition (normalize_status would map it to QUEUED, whose
            # transition fails and gets swallowed — this makes it explicit).
            if str(name).strip().upper() in _ENGINE_PHASE_STATUSES:
                self._logger.debug("task %s phase: %s", task_id, name)
                return
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

    def _task_smart_cb(self, task_id: str) -> Callable[[str], None]:
        """Live Smart-mode connection status for the UI (not persisted)."""

        def _cb(text: str) -> None:
            snap: Optional[TaskSnapshot] = None
            with self._lock:
                rec = self._tasks.get(task_id)
                if rec is None or rec.removed:
                    return
                if rec.task.smart_status != text:
                    rec.task.smart_status = text
                    snap = self._snap(rec)
            if snap:
                self._emit("updated", snap)

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
                # A worker was mid-transfer when the process died, so it goes
                # back to the queue: restoring the *status* would claim a worker
                # that does not exist.  A PAUSED task is deliberately left
                # PAUSED — the user asked for that, and the decision outlives
                # the process.  See docs/QUEUE.md §5.
                if task.status in _INTERRUPTED_STATES:
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
        mode = task.connection_mode or ""
        if not mode and self._config is not None:
            mode = getattr(self._config, "connection_mode", "") or ""
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
            "connection_mode": mode,
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

    def _remove_record_locked(self, task_id: str, save_order: bool = False) -> None:
        """Drop a task from memory and the database.

        ``save_order`` defaults to False because most callers (``remove_task``,
        ``_clear_states``) already persist the order themselves — writing it
        per removal would be quadratic when clearing many tasks.
        """
        self._tasks.pop(task_id, None)
        try:
            self._order.remove(task_id)
        except ValueError:
            pass
        try:
            self._store.delete_task(task_id)
        except Exception:
            pass
        if save_order:
            self._save_order_locked()

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
