"""Project time windows — pure logic, no I/O and no database access.

A project may declare a daily window (e.g. ``23:00`` → ``07:00``) on selected
weekdays.  Outside the window the project starts nothing new; downloads already
in flight are left alone, because killing a transfer at 07:00 would waste
everything it had already fetched.

Why this is its own module
--------------------------
Window arithmetic is where overnight schedules go wrong, and it is the part most
worth testing in isolation.  Keeping it free of SQLite and of the domain model
means ``tests/test_project_scheduling.py`` can pin every boundary — midnight,
the exact closing minute, a window that never opens on the selected days — with
plain ``datetime`` values and no fixtures.

Conventions
-----------
* Times are naive **local** datetimes.  DST is therefore not modelled; a window
  that crosses a DST jump shifts by an hour twice a year.  Documented as a known
  limitation rather than papered over with a timezone library.
* ``days`` is a list of ``0=Monday … 6=Sunday``.  An **empty list means every
  day**, which is what a user who never touches the day picker expects.
* ``start == stop`` means a 24-hour window (open all day), not "never open".
  The alternative silently stops a user's downloads forever, which is worse.
* An **unparseable** window fails **open**, for the same reason.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

MINUTES_PER_DAY = 24 * 60
DAYS_IN_WEEK = 7
# A window may be up to a week away (e.g. Sundays only), plus one day of slack
# for the overnight window that started yesterday.
_HORIZON_DAYS = 8

# Day indices use the JavaScript convention — **Sunday = 0 … Saturday = 6** —
# because that is what the UI sends (`Date.getDay()` and the `projects.day.*`
# labels).  ``datetime.weekday()`` uses Monday = 0, so every conversion goes
# through :func:`_weekday_index`.  Mixing the two conventions silently shifts a
# schedule by one day, which is exactly the bug this comment exists to prevent.
SUNDAY = 0
SATURDAY = 6

# Machine-readable reasons.  The UI maps these to translated strings; it must
# never pattern-match on human text.
REASON_NO_SCHEDULE = "no_schedule"
REASON_INVALID = "invalid_schedule"
REASON_IN_WINDOW = "in_window"
REASON_BEFORE_START = "before_start"
REASON_AFTER_STOP = "after_stop"
REASON_DAY_NOT_ALLOWED = "day_not_allowed"


def _weekday_index(moment: datetime) -> int:
    """Weekday of *moment* in the wire convention: Sunday = 0 … Saturday = 6."""
    return (moment.weekday() + 1) % DAYS_IN_WEEK


# --------------------------------------------------------------------------- #
# Time parsing
# --------------------------------------------------------------------------- #


def parse_hhmm(value: Any) -> Optional[int]:
    """``"23:00"`` → ``1380``.  ``None`` for anything unparseable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < MINUTES_PER_DAY else None
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        return None
    return hours * 60 + minutes


def format_hhmm(minutes: Optional[int]) -> str:
    if minutes is None:
        return ""
    return "%02d:%02d" % (minutes // 60 % 24, minutes % 60)


def normalize_days(days: Any) -> List[int]:
    """Deduplicated, sorted weekdays in the wire convention (Sunday = 0).

    Anything out of range is dropped rather than rejected: a stored schedule is
    data, not input, and one bad index must not make the whole window unreadable.
    """
    if isinstance(days, (str, bytes)) or not isinstance(days, Iterable):
        return []
    seen = set()
    for raw in days:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= index <= 6:
            seen.add(index)
    return sorted(seen)


def days_from_json(raw: Any) -> List[int]:
    if isinstance(raw, str):
        try:
            return normalize_days(json.loads(raw or "[]"))
        except (ValueError, TypeError):
            return []
    return normalize_days(raw)


def days_to_json(days: Any) -> str:
    return json.dumps(normalize_days(days))


def _day_allowed(schedule: "ProjectSchedule", weekday: int) -> bool:
    return not schedule.days or weekday in schedule.days


# --------------------------------------------------------------------------- #
# Value object
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProjectSchedule:
    """A project's optional daily window."""

    enabled: bool = False
    start: Optional[str] = None
    stop: Optional[str] = None
    days: List[int] = field(default_factory=list)

    @property
    def start_minutes(self) -> Optional[int]:
        return parse_hhmm(self.start)

    @property
    def stop_minutes(self) -> Optional[int]:
        return parse_hhmm(self.stop)

    @property
    def is_configured(self) -> bool:
        return self.start_minutes is not None and self.stop_minutes is not None

    @property
    def is_overnight(self) -> bool:
        start, stop = self.start_minutes, self.stop_minutes
        if start is None or stop is None:
            return False
        return stop < start

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "start": self.start or "",
            "stop": self.stop or "",
            "days": list(self.days),
            "overnight": self.is_overnight,
            "configured": self.is_configured,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ProjectSchedule":
        if isinstance(data, ProjectSchedule):
            return data
        if not isinstance(data, dict):
            return cls()
        return cls(
            enabled=bool(data.get("enabled")),
            start=(str(data.get("start")).strip() or None) if data.get("start") else None,
            stop=(str(data.get("stop")).strip() or None) if data.get("stop") else None,
            days=normalize_days(data.get("days")),
        )

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "ProjectSchedule":
        return cls(
            enabled=bool(row.get("schedule_enabled")),
            start=(str(row.get("schedule_start")).strip() or None)
            if row.get("schedule_start")
            else None,
            stop=(str(row.get("schedule_stop")).strip() or None)
            if row.get("schedule_stop")
            else None,
            days=days_from_json(row.get("schedule_days")),
        )


@dataclass(frozen=True)
class WindowDecision:
    """Whether a project's window is open, and when that will change."""

    open: bool
    reason: str
    next_change_at: Optional[float] = None
    closes_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "open": self.open,
            "reason": self.reason,
            "next_change_at": self.next_change_at,
            "closes_at": self.closes_at,
        }


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _iter_windows(
    now: datetime, schedule: ProjectSchedule, start: int, stop: int
) -> Iterable[Tuple[datetime, datetime]]:
    """Yield ``(opens, closes)`` for every window near *now*, in order.

    Starting one day back is what makes an overnight window work: at 03:00 on
    Tuesday, the window that contains us opened at 23:00 on Monday.
    """
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(-1, _HORIZON_DAYS):
        day = midnight + timedelta(days=offset)
        if not _day_allowed(schedule, _weekday_index(day)):
            continue
        opens = day + timedelta(minutes=start)
        closes = day + timedelta(minutes=stop)
        if closes <= opens:
            closes += timedelta(days=1)
        yield opens, closes


def _next_opening(
    now: datetime, schedule: ProjectSchedule, start: int, stop: int
) -> Optional[datetime]:
    candidates = [
        opens
        for opens, _ in _iter_windows(now, schedule, start, stop)
        if opens > now
    ]
    return min(candidates) if candidates else None


def evaluate(schedule: ProjectSchedule, now: Optional[datetime] = None) -> WindowDecision:
    """Is the project's window open at *now*, and what happens next?"""
    now = now or datetime.now()
    if not schedule.enabled:
        return WindowDecision(open=True, reason=REASON_NO_SCHEDULE)

    start, stop = schedule.start_minutes, schedule.stop_minutes
    if start is None or stop is None:
        # Fail OPEN.  A window this build cannot parse must not be allowed to
        # stop the user's downloads forever.
        return WindowDecision(open=True, reason=REASON_INVALID)

    if start == stop:
        # A 24-hour window: the only thing that can close it is the day filter.
        if _day_allowed(schedule, _weekday_index(now)):
            return WindowDecision(open=True, reason=REASON_IN_WINDOW)
        nxt = _next_opening(now, schedule, start, stop)
        return WindowDecision(
            open=False,
            reason=REASON_DAY_NOT_ALLOWED,
            next_change_at=nxt.timestamp() if nxt else None,
        )

    for opens, closes in _iter_windows(now, schedule, start, stop):
        if opens <= now < closes:
            return WindowDecision(
                open=True, reason=REASON_IN_WINDOW, closes_at=closes.timestamp()
            )

    nxt = _next_opening(now, schedule, start, stop)
    if not _day_allowed(schedule, _weekday_index(now)):
        reason = REASON_DAY_NOT_ALLOWED
    elif nxt is not None and nxt.date() == now.date():
        reason = REASON_BEFORE_START
    else:
        reason = REASON_AFTER_STOP
    return WindowDecision(
        open=False, reason=reason, next_change_at=nxt.timestamp() if nxt else None
    )


def is_open(schedule: ProjectSchedule, now: Optional[datetime] = None) -> bool:
    return evaluate(schedule, now).open
