"""Unified download task model with a strict, validated state machine.

The previous architecture kept task state in a handful of boolean flags spread
across the engine (``DownloadContext._cancelled``, ``_paused``, part ``done``)
and the UI queue (``TaskState`` / ``_TaskRecord``).  This module is the single
source of truth for a download's lifecycle:

* Every transition is validated against an explicit transition table, so an
  illegal mutation raises :class:`TransitionError` instead of silently
  corrupting state.
* No scattered boolean flags — a task has exactly one ``status``.
* The model is UI-agnostic (no rich/pywebview imports) so the engine, the
  queue manager, the TUI and the GUI all share the same representation.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


class TaskStatus(str, enum.Enum):
    """Canonical download lifecycle states.

    ``str``-based so values can be serialised to JSON/SQLite and displayed
    directly.  ``COMPLETED.value == "Complete"`` and
    ``DOWNLOADING.value == "Downloading"`` match the legacy UI strings the
    front-end already understands.
    """

    QUEUED = "Queued"
    ANALYZING = "Analyzing"
    STARTING = "Starting"
    DOWNLOADING = "Downloading"
    PAUSED = "Paused"
    MERGING = "Merging"
    VERIFYING = "Verifying"
    COMPLETED = "Complete"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    REMOVED = "Removed"


# States that mean "still alive" for queue accounting (counts against the
# maximum number of simultaneous downloads).
ACTIVE_STATES = frozenset(
    {
        TaskStatus.ANALYZING,
        TaskStatus.STARTING,
        TaskStatus.DOWNLOADING,
        TaskStatus.MERGING,
        TaskStatus.VERIFYING,
        TaskStatus.PAUSED,
    }
)

# States that never progress further on their own.
TERMINAL_STATES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REMOVED}
)

# Legal transitions.  Key → set of statuses the task may move *to*.
_TRANSITIONS: Dict[TaskStatus, frozenset] = {
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.ANALYZING, TaskStatus.CANCELLED, TaskStatus.REMOVED}
    ),
    TaskStatus.ANALYZING: frozenset(
        {TaskStatus.STARTING, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REMOVED}
    ),
    TaskStatus.STARTING: frozenset(
        {TaskStatus.DOWNLOADING, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REMOVED}
    ),
    TaskStatus.DOWNLOADING: frozenset(
        {
            TaskStatus.PAUSED,
            TaskStatus.MERGING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.REMOVED,
        }
    ),
    TaskStatus.PAUSED: frozenset(
        {TaskStatus.DOWNLOADING, TaskStatus.CANCELLED, TaskStatus.REMOVED}
    ),
    TaskStatus.MERGING: frozenset(
        {TaskStatus.VERIFYING, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REMOVED}
    ),
    TaskStatus.VERIFYING: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REMOVED}
    ),
    TaskStatus.COMPLETED: frozenset({TaskStatus.QUEUED, TaskStatus.REMOVED}),
    TaskStatus.FAILED: frozenset({TaskStatus.QUEUED, TaskStatus.REMOVED}),
    TaskStatus.CANCELLED: frozenset({TaskStatus.QUEUED, TaskStatus.REMOVED}),
    TaskStatus.REMOVED: frozenset(),
}

# Statuses that can be force-requeued by the crash-recovery scanner.
_RESTORABLE_TO_QUEUED = frozenset(
    {
        TaskStatus.ANALYZING,
        TaskStatus.STARTING,
        TaskStatus.DOWNLOADING,
        TaskStatus.PAUSED,
        TaskStatus.MERGING,
        TaskStatus.VERIFYING,
    }
)


def _now() -> float:
    return time.time()


class TransitionError(Exception):
    """Raised when a task is asked to make an illegal state transition."""


def is_active(status: TaskStatus) -> bool:
    return status in ACTIVE_STATES


def is_terminal(status: TaskStatus) -> bool:
    return status in TERMINAL_STATES


def normalize_status(value: Any) -> TaskStatus:
    """Coerce a stored/legacy status value into a canonical :class:`TaskStatus`.

    Legacy UI statuses from old queue files are mapped to their modern
    equivalent so persisted data from previous versions keeps loading.
    """
    if isinstance(value, TaskStatus):
        return value
    text = str(value or "").strip()
    lowered = text.lower()
    for member in TaskStatus:
        if member.value.lower() == lowered:
            return member
    legacy = {
        "stopping": TaskStatus.DOWNLOADING,  # was mid-cancel
        "stopped": TaskStatus.CANCELLED,     # was cancelled
    }
    return legacy.get(lowered, TaskStatus.QUEUED)


@dataclass
class DownloadTask:
    """Immutable-ish task record. Mutate only through the queue manager."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    url: str = ""
    filename: str = ""
    directory: str = ""
    label: str = ""

    total_size: int = 0
    downloaded_size: int = 0
    current_speed: float = 0.0
    average_speed: float = 0.0
    eta_seconds: Optional[float] = None

    status: TaskStatus = TaskStatus.QUEUED
    priority: int = 5                     # lower number = higher priority
    created_at: float = field(default_factory=_now)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    retry_count: int = 0

    error: str = ""
    connections: int = 1
    checksum: str = ""
    content_type: str = ""
    server: str = ""
    supports_range: bool = False
    etag: str = ""
    last_modified: str = ""
    category: str = "General"
    autostart: bool = True
    speed_limit_bps: int = 0
    # The resolved destination file path (after unique-name handling) once the
    # engine has computed it.  Used to scope temporary-artifact cleanup to the
    # exact files this task owns.
    resolved_path: str = ""

    def __post_init__(self) -> None:
        # Defensive: callers (JSON rows, legacy files, tests) may pass a string
        # status; always coerce to the enum so serialisation and transitions
        # never see a raw string.
        self.status = normalize_status(self.status)
        try:
            self.priority = int(self.priority)
        except (TypeError, ValueError):
            self.priority = 5
        try:
            self.connections = max(1, int(self.connections))
        except (TypeError, ValueError):
            self.connections = 1

    # ------------------------------------------------------------------ #
    # State machine
    # ------------------------------------------------------------------ #

    def can_transition(self, new_status: TaskStatus) -> bool:
        """Whether ``new_status`` is a legal next state."""
        return new_status in _TRANSITIONS.get(self.status, frozenset())

    def transition(self, new_status: TaskStatus, *, error: Optional[str] = None) -> None:
        """Move to ``new_status`` after validating the transition.

        Raises :class:`TransitionError` on illegal moves — callers must
        therefore only drive tasks through the documented lifecycle.
        """
        new_status = normalize_status(new_status)
        if new_status == self.status:
            return
        if not self.can_transition(new_status):
            raise TransitionError(
                f"task {self.id}: illegal transition {self.status.value} → {new_status.value}"
            )
        self.status = new_status
        if error is not None:
            self.error = error

    def force_status(self, new_status: TaskStatus, *, error: Optional[str] = None) -> None:
        """Unchecked status setter — reserved for crash recovery.

        The recovery scanner may find a task that was mid-flight when the
        process died; forcing it back to QUEUED is not a normal transition and
        intentionally bypasses the table.
        """
        self.status = normalize_status(new_status)
        if error is not None:
            self.error = error

    def requeue(self, *, reason: str = "") -> None:
        """Send a failed/cancelled/completed task back to the queue.

        The cumulative ``retry_count`` is intentionally preserved so callers
        (the queue manager's ``retry_task``) can increment it themselves.
        """
        if self.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.COMPLETED):
            raise TransitionError(
                f"task {self.id}: cannot requeue from {self.status.value}"
            )
        self.status = TaskStatus.QUEUED
        self.error = reason
        self.completed_at = None
        self.started_at = None

    # ------------------------------------------------------------------ #
    # Convenience markers (all validate against the transition table)
    # ------------------------------------------------------------------ #

    def mark_queued(self) -> None:
        self.transition(TaskStatus.QUEUED)

    def mark_analyzing(self) -> None:
        self.transition(TaskStatus.ANALYZING)

    def mark_starting(self) -> None:
        self.transition(TaskStatus.STARTING, error="")
        if self.started_at is None:
            self.started_at = _now()

    def mark_downloading(self) -> None:
        self.transition(TaskStatus.DOWNLOADING)

    def mark_paused(self) -> None:
        self.transition(TaskStatus.PAUSED)

    def mark_merging(self) -> None:
        self.transition(TaskStatus.MERGING)

    def mark_verifying(self) -> None:
        self.transition(TaskStatus.VERIFYING)

    def mark_completed(self) -> None:
        self.transition(TaskStatus.COMPLETED, error="")
        self.completed_at = _now()
        self.current_speed = 0.0
        self.average_speed = 0.0
        self.eta_seconds = None

    def mark_failed(self, error: str) -> None:
        self.transition(TaskStatus.FAILED, error=error)
        self.completed_at = _now()
        self.current_speed = 0.0
        self.eta_seconds = None

    def mark_cancelled(self, error: str = "Cancelled") -> None:
        self.transition(TaskStatus.CANCELLED, error=error)
        self.completed_at = _now()
        self.current_speed = 0.0
        self.eta_seconds = None

    def mark_removed(self) -> None:
        self.transition(TaskStatus.REMOVED)

    # ------------------------------------------------------------------ #
    # Derived values
    # ------------------------------------------------------------------ #

    @property
    def percent(self) -> float:
        if self.total_size <= 0:
            return 0.0
        return min(100.0, max(0.0, self.downloaded_size / self.total_size * 100.0))

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.total_size - self.downloaded_size)

    @property
    def elapsed_seconds(self) -> float:
        end = self.completed_at or _now()
        start = self.started_at or self.created_at
        return max(0.0, end - start)

    def update_speed(self, downloaded: int, now_mono: Optional[float] = None) -> None:
        """Set ``downloaded_size`` and refresh speed/ETA from the increment.

        ``current_speed`` is an exponentially-weighted moving average (stable
        during bursts) and ``average_speed`` is the whole-transfer average so
        both the "current" and "Average:" readouts are meaningful.
        """
        now_mono = now_mono if now_mono is not None else time.monotonic()
        downloaded = max(0, int(downloaded))
        if downloaded < self.downloaded_size:
            # A reset (retry) — treat as a fresh sample window.
            self.average_speed = 0.0
            self.current_speed = 0.0
        else:
            delta = downloaded - self.downloaded_size
            last = getattr(self, "_last_speed_at", now_mono)
            elapsed = now_mono - last
            if elapsed >= 0.05 and delta >= 0:
                instant = delta / elapsed if elapsed > 0 else 0.0
                if instant > 0:
                    self.current_speed = (
                        instant if self.current_speed <= 0
                        else 0.35 * instant + 0.65 * self.current_speed
                    )
            self._last_speed_at = now_mono
        self.downloaded_size = downloaded

        elapsed_total = self.elapsed_seconds
        if elapsed_total > 0 and downloaded > 0:
            self.average_speed = downloaded / elapsed_total

        remaining = self.total_size - downloaded
        if self.total_size > 0 and remaining > 0 and self.current_speed > 1.0:
            self.eta_seconds = remaining / self.current_speed
        elif self.total_size > 0 and downloaded >= self.total_size:
            self.eta_seconds = 0.0
        else:
            self.eta_seconds = None

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DownloadTask":
        """Rebuild a task from a dict (database row / JSON payload)."""
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        clean = {k: v for k, v in data.items() if k in allowed}
        clean["status"] = normalize_status(clean.get("status", TaskStatus.QUEUED.value))
        return cls(**clean)
