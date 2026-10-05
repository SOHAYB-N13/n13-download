"""Project concurrency and schedule admission policy.

Pure functions over plain numbers.  No database, no models, no filesystem — so
``tests/test_project_concurrency.py`` can prove every limit rule with a table of
tuples, and ``ui/common.py::TaskManager`` can consult the policy while holding
its own lock without any risk of re-entering I/O.

The one rule that matters
-------------------------
A task may start only when **both** a global slot and a project slot are free.
Getting this wrong in either direction is a real bug: ignoring the global limit
floods the connection pool, and ignoring the project limit lets one project
monopolise every slot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

# Machine-readable reasons.  The UI translates these; it must never match on
# human-readable text.
REASON_OK = "ok"
REASON_CLOSED = "closed"
REASON_PROJECT_PAUSED = "project_paused"
REASON_OUTSIDE_SCHEDULE = "outside_schedule"
REASON_PROJECT_LIMIT = "project_limit"
REASON_GLOBAL_LIMIT = "global_limit"


@dataclass(frozen=True)
class ProjectGate:
    """The per-project part of the admission decision.

    A frozen snapshot rather than a live lookup, because the queue consults it
    while holding its own lock: ``TaskManager._start_next`` must never perform
    I/O, so the API layer refreshes these and pushes them in — the same
    "gates are pushed, never polled" rule the queue gate follows.
    """

    paused: bool = False
    window_open: bool = True
    # 0 means "inherit the global limit".
    limit: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "paused": self.paused,
            "window_open": self.window_open,
            "limit": self.limit,
        }

    @classmethod
    def coerce(cls, value: Any) -> "ProjectGate":
        """Accept a ``ProjectGate`` or a plain dict from the bridge."""
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                paused=bool(value.get("paused")),
                window_open=bool(value.get("window_open", True)),
                limit=int(value.get("limit") or 0),
            )
        return cls()


@dataclass(frozen=True)
class AdmissionContext:
    """Everything the decision depends on, and nothing else."""

    global_active: int = 0
    global_limit: int = 1
    project_active: int = 0
    # 0 means "inherit the global limit" — the same convention the settings
    # layer uses for "unset", so the UI needs no separate tri-state.
    project_limit: int = 0
    project_paused: bool = False
    window_open: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "global_active": self.global_active,
            "global_limit": self.global_limit,
            "project_active": self.project_active,
            "project_limit": self.project_limit,
            "project_paused": self.project_paused,
            "window_open": self.window_open,
        }


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed

    def to_dict(self) -> Dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason}


def effective_project_limit(project_limit: int, global_limit: int) -> int:
    """A project limit of 0 (or nonsense) inherits the global ceiling."""
    limit = int(project_limit or 0)
    if limit > 0:
        return limit
    return max(1, int(global_limit or 1))


def may_start(ctx: AdmissionContext, *, closed: bool = False) -> AdmissionDecision:
    """May one more task of this project start right now?

    Both ceilings must have room.  The application-wide one is tested first
    because it is the harder constraint: when every global slot is busy, "the
    global limit is reached" is the truthful reason for *every* project, and
    reporting a project limit there would send the user to the wrong setting.
    The project limit is only reported once a global slot is actually free.

    The project's own pause and its schedule window come first of all: they are
    the user's explicit rules about this project, so they explain a stall better
    than a slot count ever could.
    """
    if closed:
        return AdmissionDecision(False, REASON_CLOSED)
    if ctx.project_paused:
        return AdmissionDecision(False, REASON_PROJECT_PAUSED)
    if not ctx.window_open:
        return AdmissionDecision(False, REASON_OUTSIDE_SCHEDULE)

    if int(ctx.global_active or 0) >= max(1, int(ctx.global_limit or 1)):
        return AdmissionDecision(False, REASON_GLOBAL_LIMIT)

    # Only meaningful when the project sets its own ceiling: with ``limit == 0``
    # the project inherits the global one, which was just checked.
    if int(ctx.project_limit or 0) > 0:
        limit = effective_project_limit(ctx.project_limit, ctx.global_limit)
        if int(ctx.project_active or 0) >= limit:
            return AdmissionDecision(False, REASON_PROJECT_LIMIT)

    return AdmissionDecision(True, REASON_OK)


def explain(reason: str) -> str:
    """A short English fallback for logs and the console UI.

    The graphical UI uses its own translations; this exists so a log line never
    has to print a bare enum value.
    """
    return {
        REASON_OK: "a slot is free",
        REASON_CLOSED: "the application is shutting down",
        REASON_PROJECT_PAUSED: "the project is paused",
        REASON_OUTSIDE_SCHEDULE: "outside the project's schedule window",
        REASON_PROJECT_LIMIT: "the project's concurrency limit is reached",
        REASON_GLOBAL_LIMIT: "the application concurrency limit is reached",
    }.get(reason, reason)
