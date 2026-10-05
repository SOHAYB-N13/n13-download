"""Project orchestration: the only place project rules are enforced.

Everything below the API layer that needs to *decide* something about a project
comes through here.  The service owns:

* validation and uniqueness of names;
* destination validation (delegated to :mod:`projects.validation`);
* the three delete policies and their referential-integrity guarantees;
* turning stored rows into :class:`~projects.views.ProjectView` payloads;
* the per-project gate snapshot that ``TaskManager`` consults when admitting a
  task, so the queue never has to touch SQLite while holding its lock.

It does **not** own: task state (that is ``TaskManager``), the OS shutdown path
(that is ``AutoShutdownController``), or any SQL (that is the repositories).

Concurrency note
----------------
``global_limit`` is always passed in by the caller rather than read from
``AppConfig``.  The service therefore has no opinion about application settings,
which keeps it testable with plain numbers and stops a settings change from
silently altering project behaviour.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from core.db import DEFAULT_PROJECT_ID, Database
from core.store import TaskStore
from projects import deletion, validation
from projects.admission import ProjectGate, effective_project_limit
from projects.models import (
    MAX_CONCURRENT_CEILING,
    CompletionAction,
    DeleteMode,
    Project,
    ProjectStatus,
    ProjectValidationError,
    new_project_id,
    normalize_name,
    validate_name,
)
from projects.repository import ProjectRepository
from projects.schedule import ProjectSchedule, evaluate as evaluate_window
from projects.views import ProjectCounts, ProjectView, derive_state

logger = logging.getLogger("n13")

# Statuses that mean bytes are moving.  Deleting a project mid-transfer would
# orphan a worker, so these block a delete until the caller stops them.
_TRANSFER_STATES = ("Analyzing", "Starting", "Downloading", "Merging", "Verifying")

__all__ = ["ProjectGate", "ProjectService"]


class ProjectService:
    """CRUD, policies and derived views for download projects."""

    def __init__(self, db: Database, tasks: TaskStore):
        self._db = db
        self._tasks = tasks
        self._repo = ProjectRepository(db)

    # ------------------------------------------------------------------ #
    # Default project
    # ------------------------------------------------------------------ #

    def ensure_default(self) -> Project:
        """Guarantee the Default project exists; safe to call on every start.

        The v2 migration creates it, but a database restored from a partial
        backup — or one whose rows were removed by hand — must not leave legacy
        tasks pointing at a project that is not there.
        """
        existing = self._repo.get(DEFAULT_PROJECT_ID)
        if existing is not None:
            return existing
        now = time.time()
        project = Project(
            id=DEFAULT_PROJECT_ID,
            name="Default",
            description="Downloads created before projects existed.",
            created_at=now,
            updated_at=now,
            is_default=True,
        )
        self._repo.insert(project)
        logger.info("Recreated the missing Default project")
        return project

    @property
    def default_project_id(self) -> str:
        return DEFAULT_PROJECT_ID

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def get(self, project_id: str) -> Project:
        return self._repo.require(project_id)

    def list_views(self, global_limit: int = 1, now: Optional[datetime] = None) -> List[ProjectView]:
        """Every project with its counts, progress and derived state.

        One grouped aggregate query for all projects rather than one per project:
        the dashboard needs all of them at once, and an N+1 pattern would be
        visible as a stall the moment a user has a dozen projects.
        """
        projects = self._repo.list_all()
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in self._tasks.aggregate_by_project():
            grouped.setdefault(str(row.get("project_id") or ""), []).append(row)
        stamp = now or datetime.now()
        return [
            self._view(project, grouped.get(project.id, []), global_limit, stamp)
            for project in projects
        ]

    def get_view(self, project_id: str, global_limit: int = 1) -> ProjectView:
        project = self._repo.require(project_id)
        rows = [
            row
            for row in self._tasks.aggregate_by_project()
            if str(row.get("project_id") or "") == project_id
        ]
        return self._view(project, rows, global_limit, datetime.now())

    def counts(self, project_id: str) -> ProjectCounts:
        rows = [
            row
            for row in self._tasks.aggregate_by_project()
            if str(row.get("project_id") or "") == project_id
        ]
        return ProjectCounts.from_rows(rows)

    def _view(
        self,
        project: Project,
        rows: Sequence[Dict[str, Any]],
        global_limit: int,
        now: datetime,
    ) -> ProjectView:
        counts = ProjectCounts.from_rows(rows)
        window = evaluate_window(project.schedule, now)
        state, reason = derive_state(project, counts, window)
        return ProjectView(
            project=project,
            counts=counts,
            state=state,
            state_reason=reason,
            window=window,
            effective_concurrency=effective_project_limit(
                project.concurrency_limit, global_limit
            ),
        )

    def gate_map(self, now: Optional[datetime] = None) -> Dict[str, ProjectGate]:
        """``{project_id: ProjectGate}`` for the queue's admission check."""
        stamp = now or datetime.now()
        gates: Dict[str, ProjectGate] = {}
        for project in self._repo.list_all():
            window = evaluate_window(project.schedule, stamp)
            gates[project.id] = ProjectGate(
                paused=project.is_paused,
                window_open=window.open,
                limit=project.concurrency_limit,
            )
        return gates

    def project_id_for_task(self, task_id: str) -> str:
        row = self._tasks.load_task(task_id)
        if not row:
            return DEFAULT_PROJECT_ID
        return str(row.get("project_id") or "") or DEFAULT_PROJECT_ID

    def has_active_tasks(self, project_id: str) -> bool:
        rows = self._tasks.aggregate_by_project()
        for row in rows:
            if str(row.get("project_id") or "") != project_id:
                continue
            if str(row.get("status") or "") in _TRANSFER_STATES:
                return True
        return False

    # ------------------------------------------------------------------ #
    # Create / update
    # ------------------------------------------------------------------ #

    def create(
        self,
        name: Any,
        description: Any = "",
        directory: Any = "",
        max_concurrent: int = 0,
        schedule: Any = None,
        completion_action: Any = CompletionAction.NONE,
    ) -> Project:
        cleaned = validate_name(name, self._repo.names_excluding())
        folder = validation.check_directory(directory)
        now = time.time()
        project = Project(
            id=new_project_id(),
            name=cleaned,
            description=normalize_name(description)[:500],
            directory=folder,
            created_at=now,
            updated_at=now,
            max_concurrent=_clamp_concurrency(max_concurrent),
            schedule=ProjectSchedule.from_dict(schedule),
            completion_action=_coerce_completion(completion_action),
            status=ProjectStatus.ACTIVE,
            is_default=False,
        )
        self._repo.insert(project)
        logger.info("Created project %s (%s)", project.id, project.name)
        return project

    def update(self, project_id: str, **fields: Any) -> Project:
        """Patch a project.  Only the keys present in ``fields`` change.

        Renaming is always allowed, including while the project is running:
        nothing on disk or in the database is derived from the name, so a rename
        cannot break a relationship or move a file.  Changing ``directory`` only
        affects *future* tasks — existing task rows keep the path they were
        written to, which is what makes an in-flight download survive the edit.
        """
        project = self._repo.require(project_id)

        if "name" in fields:
            project.name = validate_name(
                fields["name"], self._repo.names_excluding(project_id)
            )
        if "description" in fields:
            project.description = normalize_name(fields["description"])[:500]
        if "directory" in fields:
            project.directory = validation.check_directory(fields["directory"])
        if "max_concurrent" in fields:
            project.max_concurrent = _clamp_concurrency(fields["max_concurrent"])
        if "schedule" in fields:
            project.schedule = ProjectSchedule.from_dict(fields["schedule"])
        if "completion_action" in fields:
            project.completion_action = _coerce_completion(fields["completion_action"])
        if "status" in fields:
            project.status = _coerce_status(fields["status"])

        project.updated_at = time.time()
        self._repo.update(project)
        return project

    def rename(self, project_id: str, name: Any) -> Project:
        return self.update(project_id, name=name)

    def set_status(self, project_id: str, status: Any) -> Project:
        return self.update(project_id, status=status)

    def pause(self, project_id: str) -> Project:
        """Stop the project starting anything new.

        In-flight downloads keep running: the brief's rule is that a paused
        project gets no *new* downloads, and killing a transfer the user did not
        ask to cancel would throw away everything it had already fetched.  The
        UI says so next to the button.
        """
        return self.update(project_id, status=ProjectStatus.PAUSED)

    def resume(self, project_id: str) -> Project:
        return self.update(project_id, status=ProjectStatus.ACTIVE)

    # ------------------------------------------------------------------ #
    # Delete
    # ------------------------------------------------------------------ #

    def delete(
        self,
        project_id: str,
        mode: Any = DeleteMode.KEEP_TASKS,
        *,
        confirm_delete_files: bool = False,
    ) -> Dict[str, Any]:
        """Remove a project, honouring the caller's chosen destroy policy.

        ``KEEP_TASKS``      move the tasks to Default and drop the project row.
        ``DELETE_RECORDS``  delete the task rows; leave every file on disk.
        ``DELETE_FILES``    delete the task rows *and* the files — the only mode
                            that touches the user's data, and the only one that
                            requires ``confirm_delete_files``.

        Files are deleted **before** the rows, and a single failure aborts the
        whole operation with the project still intact.  The alternative — drop
        the rows and report a locked file afterwards — would leave the user with
        no way to retry and no record of what survived.
        """
        project = self._repo.require(project_id)
        if not project.can_delete:
            raise ProjectValidationError(
                "project_not_deletable",
                "The Default project cannot be deleted. Empty it instead.",
                "id",
            )

        # Accept both a DeleteMode and the raw string the bridge sends.  The
        # isinstance guard matters: `str(DeleteMode.KEEP_TASKS)` is
        # "DeleteMode.KEEP_TASKS", not "keep_tasks", so coercing a member that is
        # already typed raises.  The sibling coercers below all guard this way;
        # this one used to forget.
        if not isinstance(mode, DeleteMode):
            try:
                mode = DeleteMode(str(mode))
            except ValueError as exc:
                raise ProjectValidationError(
                    "invalid_delete_mode",
                    f"Unknown delete mode {mode!r}.",
                    "mode",
                ) from exc

        if self.has_active_tasks(project_id):
            raise ProjectValidationError(
                "project_busy",
                "This project has downloads in progress. Stop them first.",
                "id",
            )

        files_deleted = 0
        if mode is DeleteMode.DELETE_FILES:
            if not confirm_delete_files:
                raise ProjectValidationError(
                    "delete_files_not_confirmed",
                    "Deleting downloaded files needs explicit confirmation.",
                    "mode",
                )
            files_deleted = deletion.delete_project_files(project, self._tasks)

        with self._db.transaction() as conn:
            if mode is DeleteMode.KEEP_TASKS:
                self._tasks.reassign_project(project_id, DEFAULT_PROJECT_ID, conn=conn)
            elif mode in (DeleteMode.DELETE_RECORDS, DeleteMode.DELETE_FILES):
                self._tasks.delete_tasks_by_project(project_id, conn=conn)
            self._repo.delete(project_id, conn=conn)

        logger.info(
            "Deleted project %s (%s); mode=%s files_deleted=%d",
            project_id,
            project.name,
            mode.value,
            files_deleted,
        )
        return {
            "ok": True,
            "id": project_id,
            "mode": mode.value,
            "files_deleted": files_deleted,
        }

# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #


def _clamp_concurrency(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if number <= 0:
        return 0  # 0 = inherit the global limit
    return min(number, MAX_CONCURRENT_CEILING)


def _coerce_completion(value: Any) -> CompletionAction:
    if isinstance(value, CompletionAction):
        return value
    try:
        return CompletionAction(str(value))
    except ValueError as exc:
        raise ProjectValidationError(
            "invalid_completion_action",
            f"Unknown completion action {value!r}.",
            "completion_action",
        ) from exc


def _coerce_status(value: Any) -> ProjectStatus:
    if isinstance(value, ProjectStatus):
        return value
    try:
        return ProjectStatus(str(value))
    except ValueError as exc:
        raise ProjectValidationError(
            "invalid_status", f"Unknown project status {value!r}.", "status"
        ) from exc
