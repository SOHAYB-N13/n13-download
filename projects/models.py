"""Project domain model: identity, validation and the persisted fields.

No SQL, no filesystem, no logging.  Everything here is either a dataclass or a
function of its inputs, which is what makes the domain layer testable without a
database and keeps the rules out of the API and UI layers.

What a project *looks like right now* — its counts, its progress, its derived
state — lives in :mod:`projects.views`.  The split is deliberate: this module
describes what a project **is**, that one describes what it **shows**.  The
dependency is one-way (``views`` → ``models``), so nothing here knows that a UI
exists.

Two ideas worth stating explicitly
----------------------------------
**A project name is not an identifier.**  ``Project.id`` is an opaque token; the
name is a label the user may change at any time.  Nothing — not a path, not a
task row, not a log line — is derived from the name, so renaming can never break
a relationship or move a file.

**The persisted status is the user's intent; the effective state is derived.**
``Project.status`` records only whether the *user* paused the project.  Whether
it is running, waiting for its schedule, completed or failed is computed from the
tasks and the clock (see :func:`projects.views.derive_state`) and never written
to the database.  Persisting a derived value is how it goes stale the moment
anything changes — the same mistake ``AutoShutdownController`` deliberately
avoids.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable

from projects.schedule import (
    ProjectSchedule,
    days_to_json,
)

MAX_NAME_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 500
MAX_CONCURRENT_CEILING = 50


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class ProjectStatus(str, enum.Enum):
    """The user's intent for a project.  The only persisted status."""

    ACTIVE = "active"
    PAUSED = "paused"


class ProjectState(str, enum.Enum):
    """The project's *effective* state.  Derived on every read, never stored.

    ``SCHEDULED`` / ``WAITING_FOR_SCHEDULE`` / ``PAUSED_BY_SCHEDULE`` exist so the
    UI can tell a user *why* nothing is happening: a project held back by its own
    time window looks identical to a stalled one otherwise.
    """

    EMPTY = "empty"
    RUNNING = "running"
    QUEUED = "queued"
    SCHEDULED = "scheduled"
    WAITING_FOR_SCHEDULE = "waiting_for_schedule"
    PAUSED_BY_USER = "paused_by_user"
    PAUSED_BY_SCHEDULE = "paused_by_schedule"
    COMPLETED = "completed"
    FAILED = "failed"


class CompletionAction(str, enum.Enum):
    """What to do when every task in the project reaches a terminal state."""

    NONE = "none"
    SHUTDOWN = "shutdown"


class DeleteMode(str, enum.Enum):
    """How much a project deletion is allowed to destroy.

    There is no "delete everything" default: downloaded files are the user's
    data, and the only mode that touches them has to be asked for by name.
    """

    KEEP_TASKS = "keep_tasks"          # move the tasks to the Default project
    DELETE_RECORDS = "delete_records"  # delete task rows; leave the files on disk
    DELETE_FILES = "delete_files"      # delete task rows *and* their files


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ProjectError(Exception):
    """Base class for every project failure the UI is expected to display.

    ``code`` is machine-readable and translated by the frontend; ``message`` is
    an English fallback for logs and the console UI.  Keeping both means the
    bridge never has to ship prose, and a log line never has to print a bare
    enum.
    """

    def __init__(self, code: str, message: str = "", field: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.field = field

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": self.code,
            "message": self.message,
            "field": self.field,
        }


class ProjectNotFound(ProjectError):
    def __init__(self, project_id: str = ""):
        super().__init__(
            "project_not_found", f"No project with id {project_id!r}.", "id"
        )
        self.project_id = project_id


class ProjectValidationError(ProjectError):
    pass


# --------------------------------------------------------------------------- #
# Name handling
# --------------------------------------------------------------------------- #


def normalize_name(name: Any) -> str:
    """Trim and collapse internal whitespace so "My  Project" == "My Project"."""
    return " ".join(str(name or "").split())


def validate_name(name: Any, existing_names: Iterable[str] = ()) -> str:
    """Return the cleaned name or raise :class:`ProjectValidationError`.

    Comparison is case-insensitive: two projects called "Movies" and "movies"
    would be indistinguishable in the sidebar and impossible to tell apart in an
    error message.
    """
    cleaned = normalize_name(name)
    if not cleaned:
        raise ProjectValidationError(
            "name_required", "A project needs a name.", "name"
        )
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ProjectValidationError(
            "name_too_long",
            f"A project name may be at most {MAX_NAME_LENGTH} characters.",
            "name",
        )
    taken = {normalize_name(other).casefold() for other in existing_names}
    if cleaned.casefold() in taken:
        raise ProjectValidationError(
            "name_duplicate",
            f"A project named {cleaned!r} already exists.",
            "name",
        )
    return cleaned


def new_project_id() -> str:
    """An opaque, collision-resistant id.

    Deliberately *not* derived from the name: that is what makes renaming safe,
    and it means two projects may legitimately swap names.
    """
    return "prj_" + uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- #
# Project
# --------------------------------------------------------------------------- #


@dataclass
class Project:
    """A download project.  Persisted; mutate only through ``ProjectService``."""

    id: str
    name: str
    description: str = ""
    directory: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    max_concurrent: int = 0
    schedule: ProjectSchedule = field(default_factory=ProjectSchedule)
    completion_action: CompletionAction = CompletionAction.NONE
    status: ProjectStatus = ProjectStatus.ACTIVE
    is_default: bool = False

    def __post_init__(self) -> None:
        # Coerce whatever a database row or the bridge handed us, so the rest of
        # the code never has to defend against a raw string.
        if not isinstance(self.status, ProjectStatus):
            try:
                self.status = ProjectStatus(str(self.status))
            except ValueError:
                self.status = ProjectStatus.ACTIVE
        if not isinstance(self.completion_action, CompletionAction):
            try:
                self.completion_action = CompletionAction(str(self.completion_action))
            except ValueError:
                self.completion_action = CompletionAction.NONE
        if not isinstance(self.schedule, ProjectSchedule):
            self.schedule = ProjectSchedule.from_dict(self.schedule)
        self.is_default = bool(self.is_default)

    @property
    def is_paused(self) -> bool:
        return self.status is ProjectStatus.PAUSED

    @property
    def concurrency_limit(self) -> int:
        """0 means "inherit the global limit"."""
        try:
            return max(0, int(self.max_concurrent or 0))
        except (TypeError, ValueError):
            return 0

    @property
    def can_delete(self) -> bool:
        """The Default project is the fallback owner of legacy tasks.

        Deleting it would leave tasks with no live project, so it may be emptied
        but not removed.
        """
        return not self.is_default

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "directory": self.directory,
            "created_at": float(self.created_at or 0.0),
            "updated_at": float(self.updated_at or 0.0),
            "max_concurrent": self.concurrency_limit,
            "schedule_enabled": 1 if self.schedule.enabled else 0,
            "schedule_start": self.schedule.start,
            "schedule_stop": self.schedule.stop,
            "schedule_days": days_to_json(self.schedule.days),
            "completion_action": self.completion_action.value,
            "status": self.status.value,
            "is_default": 1 if self.is_default else 0,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Project":
        return cls(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or ""),
            description=str(row.get("description") or ""),
            directory=str(row.get("directory") or ""),
            created_at=float(row.get("created_at") or 0.0),
            updated_at=float(row.get("updated_at") or 0.0),
            max_concurrent=int(row.get("max_concurrent") or 0),
            schedule=ProjectSchedule.from_row(row),
            completion_action=CompletionAction(str(row.get("completion_action") or "none")),
            status=ProjectStatus(str(row.get("status") or "active")),
            is_default=bool(row.get("is_default")),
        )


__all__ = [
    "CompletionAction",
    "DeleteMode",
    "MAX_CONCURRENT_CEILING",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NAME_LENGTH",
    "Project",
    "ProjectError",
    "ProjectNotFound",
    "ProjectState",
    "ProjectStatus",
    "ProjectValidationError",
    "new_project_id",
    "normalize_name",
    "validate_name",
]
