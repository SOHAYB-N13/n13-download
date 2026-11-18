"""Persistent, one-shot scheduled downloads.

This is deliberately separate from :mod:`core.scheduler`: that scheduler is a
queue-wide time-of-day *gate*, while this module stores a concrete request and
fires it once at a local wall-clock time. The database is authoritative — a JS
timer can be suspended or closed and must never be the only copy of a schedule.

Lifecycle: ``scheduled → due → starting → completed`` with ``cancelled``,
``failed``, ``paused`` and ``missed`` as terminal/side states. Every transition
is persisted; ``claim_due`` is the only path from scheduled to starting and is
atomic, so two checks can never start the same row twice.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.db import Database

logger = logging.getLogger("n13")

OPEN_STATES = ("scheduled", "paused", "missed", "failed")
LIVE_STATES = ("scheduled", "due", "starting")


def resolve_due_at(local_iso: str, tz_name: str = "", now: Optional[float] = None) -> tuple[float, str]:
    """Convert a local wall-clock ``YYYY-MM-DDTHH:MM[:SS]`` to a UTC epoch.

    Returns ``(epoch_seconds, tz_name)``. Ambiguous timezone names fall back to
    the host's local zone rather than guessing UTC — the UI always says the
    schedule is in local time.
    """
    if not local_iso:
        raise ValueError("local_time_required")
    naive = datetime.fromisoformat(str(local_iso))
    if not tz_name:
        tz = datetime.now().astimezone().tzinfo or timezone.utc
        tz_name = str(getattr(tz, "key", "")) or "local"
    elif str(tz_name).upper() in ("UTC", "GMT", "UTC+00:00"):
        # Explicit UTC must not depend on the optional `tzdata` package,
        # which is absent on Windows by default.
        tz, tz_name = timezone.utc, "UTC"
    else:
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            # The browser sends this machine's IANA zone and the desktop app
            # runs on that same machine. Without `tzdata`, the OS local zone
            # is the correct interpretation of a "local time" schedule.
            tz = datetime.now().astimezone().tzinfo or timezone.utc
            tz_name = str(getattr(tz, "key", "")) or "local"
    aware = naive.replace(tzinfo=tz)
    due = aware.timestamp()
    if due <= (time.time() if now is None else now):
        raise ValueError("past_time")
    return due, tz_name


def format_local(epoch: float, tz_name: str = "") -> str:
    """Human-readable local time for a stored due timestamp."""
    try:
        if str(tz_name).upper() in ("UTC", "GMT", "UTC+00:00"):
            tz = timezone.utc
        else:
            tz = ZoneInfo(tz_name) if tz_name else datetime.now().astimezone().tzinfo
    except (ZoneInfoNotFoundError, ValueError):
        tz = None
    return datetime.fromtimestamp(epoch, tz or None).strftime("%Y-%m-%d %H:%M")


class ScheduledDownloadService:
    """SQLite-backed store of one-shot scheduled download requests."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- #
    # Writes
    # ---------------------------------------------------------------- #

    def schedule(
        self,
        payload: Dict[str, Any],
        due_at: float,
        tz_name: str = "",
        batch_id: str = "",
    ) -> str:
        if due_at <= time.time():
            raise ValueError("past_time")
        sid = "sch_" + uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock, self._db.transaction() as conn:
            conn.execute(
                """INSERT INTO scheduled_downloads
                   (id, batch_id, payload_json, due_at, tz_name, status,
                    created_at, updated_at, attempts)
                   VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?, 0)""",
                (sid, batch_id or "", json.dumps(payload, ensure_ascii=False),
                 float(due_at), tz_name or "", now, now),
            )
        return sid

    def cancel(self, sid: str) -> bool:
        """Cancel before the runner claims the row. A claimed row cannot be recalled."""
        with self._lock, self._db.transaction() as conn:
            cur = conn.execute(
                """UPDATE scheduled_downloads SET status='cancelled', updated_at=?
                   WHERE id=? AND status IN ('scheduled','paused','missed','failed')""",
                (time.time(), sid),
            )
            return cur.rowcount > 0

    def reschedule(self, sid: str, due_at: float, tz_name: str = "") -> bool:
        """Move a schedule. The same row is updated, so the old due time dies."""
        if due_at <= time.time():
            raise ValueError("past_time")
        with self._lock, self._db.transaction() as conn:
            cur = conn.execute(
                """UPDATE scheduled_downloads
                   SET status='scheduled', due_at=?, tz_name=?, updated_at=?, attempts=0,
                       last_error=''
                   WHERE id=? AND status IN ('scheduled','paused','missed','failed')""",
                (float(due_at), tz_name or "", time.time(), sid),
            )
            return cur.rowcount > 0

    def pause(self, sid: str) -> bool:
        with self._lock, self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE scheduled_downloads SET status='paused', updated_at=? WHERE id=? AND status='scheduled'",
                (time.time(), sid),
            )
            return cur.rowcount > 0

    def resume(self, sid: str) -> bool:
        with self._lock, self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE scheduled_downloads SET status='scheduled', updated_at=? WHERE id=? AND status='paused'",
                (time.time(), sid),
            )
            return cur.rowcount > 0

    def mark_started(self, sid: str, task_id: str) -> bool:
        with self._lock, self._db.transaction() as conn:
            cur = conn.execute(
                """UPDATE scheduled_downloads
                   SET status='completed', task_id=?, started_at=?, finished_at=?, updated_at=?
                   WHERE id=? AND status='starting'""",
                (task_id, time.time(), time.time(), time.time(), sid),
            )
            return cur.rowcount > 0

    def mark_failed(self, sid: str, error: str) -> bool:
        with self._lock, self._db.transaction() as conn:
            cur = conn.execute(
                """UPDATE scheduled_downloads
                   SET status='failed', last_error=?, finished_at=?, updated_at=?
                   WHERE id=? AND status='starting'""",
                (str(error)[:500], time.time(), time.time(), sid),
            )
            return cur.rowcount > 0

    # ---------------------------------------------------------------- #
    # Reads / claim
    # ---------------------------------------------------------------- #

    def get(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._db.lock:
            row = self._db.execute("SELECT * FROM scheduled_downloads WHERE id=?", (sid,)).fetchone()
        return self._row(row) if row else None

    def list_all(self, status: str = "") -> List[Dict[str, Any]]:
        sql = "SELECT * FROM scheduled_downloads"
        params: tuple = ()
        if status:
            sql += " WHERE status=?"
            params = (status,)
        sql += " ORDER BY due_at ASC, created_at ASC"
        with self._db.lock:
            rows = self._db.execute(sql, params).fetchall()
        return [self._row(r) for r in rows]

    def claim_due(self, now: Optional[float] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Atomically take up to *limit* due rows into ``starting``.

        The UPDATE is inside one transaction guarded by the service lock, so a
        second check cannot see the same row as ``scheduled`` once claimed.
        """
        now = time.time() if now is None else float(now)
        claimed: List[Dict[str, Any]] = []
        with self._lock, self._db.transaction() as conn:
            rows = conn.execute(
                """SELECT * FROM scheduled_downloads
                   WHERE status='scheduled' AND due_at<=?
                   ORDER BY due_at ASC LIMIT ?""",
                (now, max(1, int(limit))),
            ).fetchall()
            for row in rows:
                cur = conn.execute(
                    """UPDATE scheduled_downloads
                       SET status='starting', updated_at=?, attempts=attempts+1
                       WHERE id=? AND status='scheduled'""",
                    (now, row["id"]),
                )
                if cur.rowcount:
                    claimed.append(self._row(row))
                    claimed[-1]["status"] = "starting"
        return claimed

    def recover_on_startup(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Explicit missed-schedule policy, applied once per process start.

        Due rows left behind by a *previous* process are NOT started: a user
        who was away for a day must not come back to a flood of old downloads.
        They become ``missed`` and are surfaced for an explicit decision.
        Rows already claimed (``starting``) were interrupted mid-flight and are
        marked ``failed`` with a clear reason.
        """
        now = time.time() if now is None else float(now)
        missed: List[Dict[str, Any]] = []
        with self._lock, self._db.transaction() as conn:
            conn.execute(
                """UPDATE scheduled_downloads
                   SET status='failed', last_error='Interrupted by application exit',
                       finished_at=?, updated_at=? WHERE status='starting'""",
                (now, now),
            )
            rows = conn.execute(
                "SELECT * FROM scheduled_downloads WHERE status='scheduled' AND due_at<=?",
                (now,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """UPDATE scheduled_downloads SET status='missed', updated_at=? WHERE id=?""",
                    (now, row["id"]),
                )
                item = self._row(row)
                item["status"] = "missed"
                missed.append(item)
        return missed

    @staticmethod
    def _row(row: Any) -> Dict[str, Any]:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json", "{}") or "{}")
        except (ValueError, TypeError):
            item["payload"] = {}
        item["local_time"] = format_local(float(item.get("due_at", 0)), item.get("tz_name", ""))
        return item


class ScheduledRunner:
    """Single backend loop that fires due schedules. No frontend timer involved."""

    def __init__(
        self,
        service: ScheduledDownloadService,
        on_due: Callable[[Dict[str, Any]], Any],
        interval: float = 1.0,
        now_fn: Optional[Callable[[], float]] = None,
        logger_obj: Optional[object] = None,
    ) -> None:
        self._service = service
        self._on_due = on_due
        self._interval = max(0.05, float(interval))
        self._now = now_fn or time.time
        self._log = logger_obj or logger
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()

    def start(self) -> None:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="n13-scheduled-downloads", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.tick_once()

    def tick_once(self) -> int:
        """Fire all due rows. Exposed for deterministic tests and startup."""
        fired = 0
        for item in self._service.claim_due(now=self._now()):
            # Re-check after claiming: a cancel that raced with the claim wins.
            current = self._service.get(item["id"])
            if not current or current["status"] != "starting":
                continue
            try:
                task_id = self._on_due(item)
                if not task_id:
                    raise RuntimeError("scheduled_start_returned_no_task")
                self._service.mark_started(item["id"], str(task_id))
                fired += 1
            except Exception as exc:
                self._service.mark_failed(item["id"], str(exc))
                try:
                    self._log.warning("Scheduled download %s failed to start: %s", item["id"], exc)
                except Exception:
                    pass
        return fired
