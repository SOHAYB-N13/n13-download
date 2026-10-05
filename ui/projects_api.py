"""Project bridge methods, mixed into :class:`ui.api.Api`.

Why a mixin instead of just more methods on ``Api``
---------------------------------------------------
``ui/api.py`` is already the largest file in the repository.  Adding fifteen
project methods there would make it a god module by the numbers *and* in
practice: the bridge would end up holding project policy, task orchestration and
event routing at the same time.  This module owns the project-facing bridge
surface and nothing else.  ``Api`` inherits it, so pywebview still sees one flat
set of ``window.pywebview.api`` methods — which is what the frontend and the
existing tests expect.

Response contract
-----------------
Every method here returns a JSON object with an ``ok`` flag::

    {"ok": True,  ...payload}
    {"ok": False, "error": "name_duplicate", "message": "...", "field": "name"}

The frontend therefore has exactly **one** project response shape.  ``message``
is an English fallback for logs; the UI translates the machine-readable
``error`` code, so this layer never ships user-facing prose.

Wiring
------
``_create_projects`` is the composition root for the whole project layer: it
builds the shared store, the service, and the initial gate snapshot *before*
``TaskManager`` exists, so a project the user paused is already blocked when the
queue restores its downloads.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.store import TaskStore
from core.task import TaskStatus as TaskState
from projects.models import CompletionAction, ProjectError
from projects.service import ProjectService

log = logging.getLogger("n13")

# States in which a download is actually moving.  Used to decide whether a
# project can be deleted, and which tasks "Stop project" should cancel.
_LIVE_STATES = (
    TaskState.ANALYZING,
    TaskState.STARTING,
    TaskState.DOWNLOADING,
    TaskState.MERGING,
    TaskState.VERIFYING,
)

# Events after which a project may have just run out of work.
_COMPLETION_EVENTS = ("finished", "removed")


class ProjectsApiMixin:
    """Project-facing bridge methods.  Mixed into ``Api``."""

    # Populated by ``_create_projects``; declared here for readers and type
    # checkers, which cannot see through the mixin.
    _projects: ProjectService
    _store: TaskStore

    # ------------------------------------------------------------------ #
    # Composition root
    # ------------------------------------------------------------------ #

    def _create_projects(self, db_path: Path) -> Dict[str, Any]:
        """Build the project layer and return the initial gate snapshot.

        Called before ``TaskManager`` is constructed, because the manager starts
        restored downloads from its constructor.  Handing it the gates up front
        is what makes "a paused project stays paused across a restart" true
        rather than merely likely.
        """
        self._store = TaskStore(Path(db_path))
        self._projects = ProjectService(self._store.db, self._store)
        self._projects.ensure_default()
        return self._projects.gate_map()

    # ------------------------------------------------------------------ #
    # Gate refresh
    # ------------------------------------------------------------------ #

    def refresh_project_gates(self) -> Dict[str, Any]:
        """Recompute the per-project gate snapshot and push it into the queue.

        Called on every project mutation, on every add/remove, and from the
        scheduler's 15-second tick so a window that opens on the clock is noticed
        without this layer starting a second timer thread.
        """
        projects = getattr(self, "_projects", None)
        manager = getattr(self, "_manager", None)
        if projects is None or manager is None:
            return {}
        try:
            gates = projects.gate_map()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Could not refresh project gates: %s", exc)
            return {}
        manager.set_project_gates(gates)
        return {pid: gate.to_dict() for pid, gate in gates.items()}

    def _global_concurrency(self) -> int:
        try:
            return max(1, int(self._manager.max_concurrent))
        except Exception:
            return 1

    def _projects_changed(self) -> None:
        """Tell the UI to re-read the project list without waiting for a poll."""
        self._event_queue.put_nowait({"type": "projects_changed"})

    # ------------------------------------------------------------------ #
    # Completion actions
    # ------------------------------------------------------------------ #

    def maybe_run_completion_action(self, event: str, snapshot: Any) -> None:
        """Run a project's completion action once its last task is done.

        Feeds the **existing** auto-shutdown controller rather than starting a
        second shutdown path, so the countdown, the cancellation, the crash
        marker and the "never power off while other work remains" rule are all
        the ones already tested.  See ``AutoShutdownController.
        arm_for_project_completion``.
        """
        if event not in _COMPLETION_EVENTS:
            return
        project_id = str(getattr(snapshot, "project_id", "") or "")
        if not project_id:
            return
        try:
            project = self._projects.get(project_id)
        except ProjectError:
            return
        if project.completion_action is not CompletionAction.SHUTDOWN:
            return
        try:
            if self._projects.has_unfinished_tasks(project_id):
                return
        except Exception:  # pragma: no cover - defensive
            return
        controller = getattr(self, "_auto_shutdown", None)
        if controller is None:
            return
        log.info(
            "Project %s (%s) has no work left; running its completion action",
            project.name,
            project.id,
        )
        controller.arm_for_project_completion(project_id)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def get_projects(self) -> Dict[str, Any]:
        """Every project with counts, progress and derived state."""
        try:
            views = self._projects.list_views(self._global_concurrency())
        except ProjectError as exc:
            return exc.to_dict()
        return {"ok": True, "projects": [view.to_dict() for view in views]}

    def get_project(self, project_id: str) -> Dict[str, Any]:
        """One project, its tasks, and why its queued work is or is not running."""
        try:
            view = self._projects.get_view(project_id, self._global_concurrency())
        except ProjectError as exc:
            return exc.to_dict()
        payload = view.to_dict()
        payload["tasks"] = [
            snap.to_dict()
            for snap in self._manager.snapshots()
            if snap.project_id == project_id
        ]
        payload["admission"] = self._project_admission(project_id)
        return {"ok": True, "project": payload}

    def get_project_gates(self) -> Dict[str, Any]:
        """The admission snapshot the queue is currently using (diagnostics)."""
        gates = self.refresh_project_gates()
        return {"ok": True, "gates": gates}

    def get_project_admission(self, task_id: str) -> Dict[str, Any]:
        """Why one task is or is not allowed to start."""
        return {"ok": True, "admission": self._manager.project_admission(task_id)}

    def _project_admission(self, project_id: str) -> Dict[str, Any]:
        """The live admission verdict for a project's first waiting task.

        Reports on a real queued task rather than on a synthetic context, so the
        answer is the one the scheduler would actually apply.
        """
        for snap in self._manager.snapshots():
            if snap.project_id == project_id and snap.state is TaskState.QUEUED:
                return self._manager.project_admission(snap.id)
        return {"allowed": True, "reason": "ok"}

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    def create_project(
        self,
        name: str,
        description: str = "",
        directory: str = "",
        max_concurrent: int = 0,
        schedule: Optional[Dict[str, Any]] = None,
        completion_action: str = "none",
    ) -> Dict[str, Any]:
        try:
            project = self._projects.create(
                name,
                description,
                directory,
                max_concurrent,
                schedule,
                completion_action,
            )
        except ProjectError as exc:
            return exc.to_dict()
        self.refresh_project_gates()
        self._projects_changed()
        return {"ok": True, "project": self._view_dict(project.id)}

    def update_project(self, project_id: str, fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            self._projects.update(project_id, **(fields or {}))
        except ProjectError as exc:
            return exc.to_dict()
        self.refresh_project_gates()
        self._projects_changed()
        return {"ok": True, "project": self._view_dict(project_id)}

    def rename_project(self, project_id: str, name: str) -> Dict[str, Any]:
        """Rename is always allowed, including mid-download.

        Nothing on disk or in the database is derived from a project's name, so a
        rename cannot break a task relationship or move a file.
        """
        return self.update_project(project_id, {"name": name})

    def pause_project(self, project_id: str) -> Dict[str, Any]:
        """Stop the project starting anything new.  Running downloads continue."""
        return self.update_project(project_id, {"status": "paused"})

    def resume_project(self, project_id: str) -> Dict[str, Any]:
        return self.update_project(project_id, {"status": "active"})

    def delete_project(
        self,
        project_id: str,
        mode: str = "keep_tasks",
        confirm_files: bool = False,
    ) -> Dict[str, Any]:
        """Delete a project under an explicit destroy policy.

        ``keep_tasks`` moves its tasks to the Default project, ``delete_records``
        removes the task rows but leaves every file on disk, and ``delete_files``
        removes the files too — the only mode that touches the user's data, and
        the only one that needs ``confirm_files``.
        """
        try:
            result = self._projects.delete(
                project_id, mode, confirm_delete_files=bool(confirm_files)
            )
        except ProjectError as exc:
            return exc.to_dict()
        self.refresh_project_gates()
        self._projects_changed()
        return result

    def stop_project_tasks(self, project_id: str) -> Dict[str, Any]:
        """Cancel every in-flight download in a project, so it can be deleted.

        Cancelling rather than removing keeps the rows intact: destroying data
        stays the job of an explicit delete mode, one step later.
        """
        try:
            self._projects.get(project_id)
        except ProjectError as exc:
            return exc.to_dict()
        stopped: List[str] = []
        for snap in self._manager.snapshots():
            if snap.project_id != project_id:
                continue
            if snap.state in _LIVE_STATES:
                self._manager.cancel_task(snap.id)
                stopped.append(snap.id)
        return {"ok": True, "stopped": stopped}

    def assign_tasks_to_project(
        self, task_ids: Iterable[str], project_id: str
    ) -> Dict[str, Any]:
        """Move existing tasks into a project."""
        try:
            self._projects.get(project_id)
        except ProjectError as exc:
            return exc.to_dict()
        moved: List[str] = []
        for task_id in list(task_ids or []):
            if self._manager.set_task_project(str(task_id), project_id):
                moved.append(str(task_id))
        if moved:
            self.refresh_project_gates()
            self._projects_changed()
        return {"ok": True, "moved": moved}

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _view_dict(self, project_id: str) -> Dict[str, Any]:
        return self._projects.get_view(
            project_id, self._global_concurrency()
        ).to_dict()

    def project_directory(self, project_id: str) -> str:
        """A project's destination folder, or ``""`` when it has none.

        Used by the add flow so a download added to a project lands where the
        project says, without the caller having to look the path up.
        """
        if not project_id:
            return ""
        try:
            return self._projects.get(project_id).directory
        except ProjectError:
            return ""
