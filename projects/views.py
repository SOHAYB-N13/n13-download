"""Project read model: what the UI is shown, derived from stored rows.

Split out of :mod:`projects.models` because it answers a different question.
``models.py`` describes **what a project is** — its identity, its validation, the
fields that are persisted.  This module describes **what a project looks like
right now** — its counts, its progress, and the single state-derivation rule.

Nothing here is persisted.  ``Project.status`` records only the user's pause;
whether a project is running, waiting for its schedule, completed or failed is
computed on every read.  Persisting a derived value is how it goes stale the
moment anything changes — the same mistake ``AutoShutdownController``
deliberately avoids.

Dependency direction is one-way: ``views`` imports from ``models``, never the
reverse.  That is what keeps ``models.py`` free of any notion of presentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Tuple

from core.task import TaskStatus
from projects.models import (
    Project,
    ProjectState,
)
from projects.schedule import (
    REASON_AFTER_STOP,
    REASON_BEFORE_START,
    REASON_DAY_NOT_ALLOWED,
    REASON_IN_WINDOW,
    REASON_INVALID,
    REASON_NO_SCHEDULE,
    WindowDecision,
)

# Task states that mean "bytes are moving right now".  Paused is deliberately
# excluded: it is a distinct bucket in the project counts, because "3 paused"
# and "3 downloading" are very different things to a user staring at a stalled
# project.
_TRANSFER_STATES = frozenset(
    {
        TaskStatus.ANALYZING,
        TaskStatus.STARTING,
        TaskStatus.DOWNLOADING,
        TaskStatus.MERGING,
        TaskStatus.VERIFYING,
    }
)


# --------------------------------------------------------------------------- #
# Task aggregation
# --------------------------------------------------------------------------- #


@dataclass
class ProjectCounts:
    """Task counts per status bucket, plus byte progress."""

    total: int = 0
    completed: int = 0
    queued: int = 0
    active: int = 0
    failed: int = 0
    paused: int = 0
    cancelled: int = 0
    removed: int = 0
    downloaded_bytes: int = 0
    total_bytes: int = 0

    @property
    def finished(self) -> int:
        return self.completed + self.failed + self.cancelled + self.removed

    @property
    def pending(self) -> int:
        """Work that can still make progress on its own."""
        return self.queued + self.active + self.paused

    @property
    def progress_percent(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(100.0, max(0.0, self.downloaded_bytes / self.total_bytes * 100.0))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "completed": self.completed,
            "queued": self.queued,
            "active": self.active,
            "failed": self.failed,
            "paused": self.paused,
            "cancelled": self.cancelled,
            "removed": self.removed,
            "finished": self.finished,
            "pending": self.pending,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "progress_percent": round(self.progress_percent, 2),
        }

    @classmethod
    def from_rows(cls, rows: Iterable[Dict[str, Any]]) -> "ProjectCounts":
        """Build from ``aggregate_by_project()`` rows for one project.

        Unknown status strings are counted in ``total`` but no bucket, so a task
        written by a future version is never invisible.
        """
        counts = cls()
        for row in rows:
            n = int(row.get("n") or 0)
            counts.total += n
            counts.downloaded_bytes += int(row.get("done") or 0)
            counts.total_bytes += int(row.get("total") or 0)
            try:
                status = TaskStatus(str(row.get("status") or ""))
            except ValueError:
                continue
            if status is TaskStatus.QUEUED:
                counts.queued += n
            elif status is TaskStatus.PAUSED:
                counts.paused += n
            elif status in _TRANSFER_STATES:
                counts.active += n
            elif status is TaskStatus.COMPLETED:
                counts.completed += n
            elif status is TaskStatus.FAILED:
                counts.failed += n
            elif status is TaskStatus.CANCELLED:
                counts.cancelled += n
            elif status is TaskStatus.REMOVED:
                counts.removed += n
        return counts


# --------------------------------------------------------------------------- #
# Derived state
# --------------------------------------------------------------------------- #


def derive_state(
    project: Project, counts: ProjectCounts, window: WindowDecision
) -> Tuple[ProjectState, str]:
    """``(ProjectState, reason)`` — the single place this precedence is defined.

    Order matters and is documented in ``docs/PROJECTS.md`` §7.  The short
    version: user intent wins over everything, a moving transfer wins over the
    schedule (a closed window never stops a running download), and the schedule
    explains a stall before "completed" or "failed" get a chance to claim it.
    """
    if project.is_paused:
        return ProjectState.PAUSED_BY_USER, "project_paused"
    if counts.total == 0:
        return ProjectState.EMPTY, "no_tasks"
    if counts.active > 0:
        return ProjectState.RUNNING, "active_tasks"

    scheduled = project.schedule.enabled
    if scheduled and not window.open:
        if counts.queued > 0:
            return ProjectState.PAUSED_BY_SCHEDULE, window.reason
        if counts.pending > 0:
            return ProjectState.WAITING_FOR_SCHEDULE, window.reason
    if scheduled and window.open and counts.queued > 0:
        return ProjectState.SCHEDULED, REASON_IN_WINDOW

    if counts.pending > 0:
        return ProjectState.QUEUED, "waiting_for_slot"
    if counts.failed > 0:
        return ProjectState.FAILED, "has_failures"
    if counts.completed > 0:
        return ProjectState.COMPLETED, "all_complete"
    return ProjectState.COMPLETED, "nothing_left"


@dataclass
class ProjectView:
    """A project plus everything derived, as the bridge hands it to the UI.

    One flat shape, deliberately.  The frontend has three different payload
    shapes to deal with already (task snapshot / history entry / settings dict),
    and feeding the wrong one to a renderer produces bugs that look real — so the
    project layer ships exactly one.
    """

    project: Project
    counts: ProjectCounts
    state: ProjectState
    state_reason: str
    window: WindowDecision
    effective_concurrency: int = 0

    def to_dict(self) -> Dict[str, Any]:
        project = self.project
        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "directory": project.directory,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "status": project.status.value,
            "is_default": project.is_default,
            "completion_action": project.completion_action.value,
            "max_concurrent": project.concurrency_limit,
            "effective_concurrency": self.effective_concurrency,
            "can_delete": project.can_delete,
            "state": self.state.value,
            "state_reason": self.state_reason,
            "schedule": project.schedule.to_dict(),
            "schedule_open": self.window.open,
            "schedule_reason": self.window.reason,
            "schedule_closes_at": self.window.closes_at,
            "schedule_next_change_at": self.window.next_change_at,
            "counts": self.counts.to_dict(),
        }


# Reasons the UI is allowed to receive from ``derive_state``.  A test asserts
# that every ``projects.reason.*`` i18n key exists for exactly this set, so a new
# branch cannot ship without a translation.
STATE_REASONS = (
    "project_paused",
    "no_tasks",
    "active_tasks",
    "waiting_for_slot",
    "has_failures",
    "all_complete",
    "nothing_left",
    REASON_NO_SCHEDULE,
    REASON_INVALID,
    REASON_IN_WINDOW,
    REASON_DAY_NOT_ALLOWED,
    REASON_BEFORE_START,
    REASON_AFTER_STOP,
)


__all__ = [
    "ProjectCounts",
    "ProjectView",
    "STATE_REASONS",
    "derive_state",
]
