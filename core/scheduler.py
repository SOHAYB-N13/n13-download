"""Download scheduler (Phase 11).

Runs one lightweight daemon thread that periodically applies three rules:

* **Start window** — the queue stays gated (no new downloads start) until
  ``schedule_start_time`` (daily, "HH:MM").  An optional ``schedule_days`` list
  limits the window to specific weekdays; on other days the queue runs freely.
* **Stop window** — the queue is gated again from ``schedule_stop_time``.
* **Night speed cap** — between ``night_start_time`` and ``night_end_time`` the
  global bandwidth limiter is overridden to ``night_speed_limit_bps`` so e.g.
  the connection can run unlimited at 23:00 and drop to 512 KB/s at 07:00.

The scheduler never touches download state directly — it only flips a gate on
the queue manager and adjusts the shared limiter, so nothing here can corrupt a
running download.
"""

from __future__ import annotations

import threading
from datetime import datetime, time as dtime
from typing import Callable, Optional

# Seconds between scheduler ticks.
_TICK_INTERVAL = 15.0


def _parse_hhmm(value: Optional[str]) -> Optional[dtime]:
    if not value:
        return None
    try:
        h, m = value.strip().split(":", 1)
        return dtime(int(h), int(m))
    except (ValueError, TypeError):
        return None


def _now_time() -> dtime:
    return datetime.now().time().replace(microsecond=0)


def _in_window(now: dtime, start: Optional[dtime], end: Optional[dtime]) -> bool:
    """True when *now* falls inside [start, end), handling midnight wrap."""
    if start is None or end is None:
        return False
    if start < end:
        return start <= now < end
    # Window wraps past midnight (e.g. 23:00 -> 07:00).
    return now >= start or now < end


# Index matches datetime.weekday() (0 = Monday).
_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _day_allowed(cfg) -> bool:
    """True when the start/stop window applies today.

    An empty/unset ``schedule_days`` means every day, which keeps the original
    behaviour for configs written before the setting existed.  On a day the user
    did not select the window is simply not applied, so the queue runs freely.
    """
    days = getattr(cfg, "schedule_days", None)
    if not days:
        return True
    try:
        today = _DAY_NAMES[datetime.now().weekday()]
    except Exception:
        return True
    return today in {str(d).strip().lower() for d in days}


class Scheduler:
    """Periodic queue-gate + bandwidth-limit scheduler."""

    def __init__(
        self,
        config,
        on_gate: Optional[Callable[[bool], None]] = None,
        on_speed: Optional[Callable[[int], None]] = None,
        on_tick: Optional[Callable[[], None]] = None,
        logger: Optional[object] = None,
    ) -> None:
        self._config = config
        self._on_gate = on_gate or (lambda on: None)
        self._on_speed = on_speed or (lambda bps: None)
        # A second clock for callers that need periodic work but must not start
        # their own thread.  The project layer uses it to re-evaluate each
        # project's time window: a window opens on the wall clock, so the queue
        # has to be told even when nothing else happens.  Deliberately a hook
        # rather than "the project layer piggybacks on on_gate", so disabling the
        # scheduler cannot silently stop project schedules from being applied.
        self._on_tick = on_tick or (lambda: None)
        self._logger = logger
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="n13-scheduler", daemon=True
        )
        self._thread.start()
        # Apply immediately so a change takes effect without waiting a tick.
        self._tick()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    # ------------------------------------------------------------------ #
    # Tick
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        while not self._stop.wait(_TICK_INTERVAL):
            try:
                self._tick()
            except Exception as exc:
                if self._logger is not None:
                    try:
                        self._logger.warning("Scheduler tick failed: %s", exc)
                    except Exception:
                        pass

    def _tick(self) -> None:
        try:
            self._on_tick()
        except Exception as exc:
            if self._logger is not None:
                try:
                    self._logger.warning("Scheduler tick hook failed: %s", exc)
                except Exception:
                    pass

        cfg = self._config
        enabled = bool(getattr(cfg, "scheduler_enabled", False))
        if not enabled:
            self._on_gate(False)
            self._on_speed(int(getattr(cfg, "max_speed_bps", 0) or 0))
            return

        now = _now_time()
        gate = False

        start = _parse_hhmm(getattr(cfg, "schedule_start_time", None))
        stop = _parse_hhmm(getattr(cfg, "schedule_stop_time", None))
        # Only gate on days the window applies to; other days run unrestricted.
        if _day_allowed(cfg):
            if start is not None and now < start:
                gate = True
            if stop is not None and now >= stop:
                gate = True
        self._on_gate(gate)

        bps = int(getattr(cfg, "max_speed_bps", 0) or 0)
        night_cap = int(getattr(cfg, "night_speed_limit_bps", 0) or 0)
        night_start = _parse_hhmm(getattr(cfg, "night_start_time", None))
        night_end = _parse_hhmm(getattr(cfg, "night_end_time", None))
        if night_cap > 0 and _in_window(now, night_start, night_end):
            bps = night_cap
        self._on_speed(bps)
