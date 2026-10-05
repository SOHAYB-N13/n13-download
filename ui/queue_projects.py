"""Project-aware admission for the queue.

Extracted from ``ui/common.py`` so that file keeps owning *the queue* and this one
owns *the project integration*.  The split is the same shape as
``ui/projects_api.py::ProjectsApiMixin``: a cohesive slice of a large class,
reviewable on its own, mixed back in so every existing call site is unchanged.

What lives here
---------------
* slot accounting — one counter per project, plus the global total;
* the admission verdict — "may this task start right now?" — delegated to the
  pure policy in :mod:`projects.admission`;
* the per-project gate snapshot the API layer pushes in.

Contract with the host class
----------------------------
This is a mixin, not a standalone object.  It expects ``TaskManager`` to provide:

``_lock``               an ``RLock`` guarding all of the state below
``_active``             the global running-worker count
``_active_by_project``  ``{project_id: running count}``
``_project_gates``      ``{project_id: ProjectGate}``, replaced wholesale
``_max_concurrent``     the application-wide ceiling
``_closed``             True once the manager is shutting down
``_tasks``              the task registry
``_start_next``         the queue's single start loop
``_save_task_locked``   persistence, caller already holding the lock

Everything here assumes the caller holds ``_lock`` unless the method takes it
itself — the ``_locked`` suffix marks the ones that do not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from core.db import DEFAULT_PROJECT_ID
from projects.admission import (
    AdmissionContext,
    AdmissionDecision,
    ProjectGate,
    may_start,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, never executed
    from ui.common import _TaskRecord

__all__ = ["ProjectAdmissionMixin"]


class ProjectAdmissionMixin:
    """Project-aware slot accounting and admission.  Mixed into ``TaskManager``."""

    # ------------------------------------------------------------------
    # Slot accounting
    # ------------------------------------------------------------------

    def _acquire_slot(self, project_id: str) -> None:
        """Claim one concurrency slot.  Callers must hold ``self._lock``."""
        self._active += 1
        key = project_id or DEFAULT_PROJECT_ID
        self._active_by_project[key] = self._active_by_project.get(key, 0) + 1

    def _release_slot(self, project_id: str) -> None:
        """Return one concurrency slot.  Callers must hold ``self._lock``.

        Keeps the per-project map free of zero entries so a long session does not
        accumulate a key for every project the user ever ran.
        """
        self._active = max(0, self._active - 1)
        key = project_id or DEFAULT_PROJECT_ID
        remaining = self._active_by_project.get(key, 0) - 1
        if remaining > 0:
            self._active_by_project[key] = remaining
        else:
            self._active_by_project.pop(key, None)

    def project_active_count(self, project_id: str) -> int:
        """Live worker threads currently owned by a project."""
        with self._lock:
            return self._active_by_project.get(project_id or DEFAULT_PROJECT_ID, 0)

    def project_active_counts(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._active_by_project)

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    @property
    def project_gates(self) -> Dict[str, ProjectGate]:
        with self._lock:
            return dict(self._project_gates)

    def set_project_gates(self, gates: Dict[str, Any]) -> None:
        """Replace the per-project admission snapshot, then try to start work.

        Pushed by the API layer on every project change and on a timer.  The
        manager deliberately does **not** read the database here: ``_start_next``
        runs while holding ``self._lock``, and doing I/O under that lock is the
        deadlock shape the queue was refactored to remove.
        """
        with self._lock:
            self._project_gates = {
                str(pid): ProjectGate.coerce(gate) for pid, gate in (gates or {}).items()
            }
        self._start_next()

    # ------------------------------------------------------------------
    # Admission
    # ------------------------------------------------------------------

    def _admission_locked(self, rec: _TaskRecord) -> AdmissionDecision:
        """Whether this task's project lets it start right now.

        An unknown project is treated as open and unlimited.  That direction is
        deliberate: blocking on missing information would stall the whole queue
        the first time a project row failed to load.
        """
        project_id = rec.task.project_id or DEFAULT_PROJECT_ID
        gate = self._project_gates.get(project_id)
        return may_start(
            AdmissionContext(
                global_active=self._active,
                global_limit=self._max_concurrent,
                project_active=self._active_by_project.get(project_id, 0),
                project_limit=gate.limit if gate else 0,
                project_paused=gate.paused if gate else False,
                window_open=gate.window_open if gate else True,
            ),
            closed=self._closed,
        )

    def _project_blocks_locked(self, rec: _TaskRecord) -> bool:
        """Whether the *project itself* forbids a start.

        Narrower than :meth:`_admission_locked`: a full global slot pool is not a
        project constraint, so it must not be reported as one.  Only the three
        rules the user set on the project count — its pause, its schedule window,
        and its own concurrency ceiling.
        """
        project_id = rec.task.project_id or DEFAULT_PROJECT_ID
        gate = self._project_gates.get(project_id)
        if gate is None:
            return False
        if gate.paused or not gate.window_open:
            return True
        if gate.limit > 0 and self._active_by_project.get(project_id, 0) >= gate.limit:
            return True
        return False

    def project_admission(self, task_id: str) -> Dict[str, Any]:
        """Why a task is (or is not) allowed to start — for the UI, computed live."""
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None:
                return AdmissionDecision(False, "unknown_task").to_dict()
            return self._admission_locked(rec).to_dict()

    # ------------------------------------------------------------------
    # Task moves
    # ------------------------------------------------------------------

    def set_task_project(self, task_id: str, project_id: str) -> bool:
        """Move a task to another project.

        A pure relabel: the file on disk, the task's own ``directory`` and its
        status are all untouched, so a running download is not disturbed and
        nothing has to be re-analysed.  Returns False for an unknown task.
        """
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None:
                return False
            rec.task.project_id = project_id or DEFAULT_PROJECT_ID
            self._save_task_locked(rec)
        return True
