"""Smart Download Optimizer.

Decides how many download connections/segments to use for a download instead
of always relying on a fixed user-selected number, and adapts that parallelism
safely while the download runs.

Responsibilities
----------------
* **Initial selection** — pick a segment count from file size + Range support,
  capped by a configurable Smart maximum.  No-Range servers stay on the safe
  single-stream path.
* **Adaptive scaling** — control how many of the (already-built) segments run
  concurrently through a thread-safe :class:`ConnectionGovernor`.  When the
  server is stable and throughput improves, parallelism is increased gradually;
  when it plateaus, increases stop; near completion it becomes conservative.
* **Server backoff** — on 429/503/502/timeout/reset, parallelism is reduced and
  a cooldown prevents it from immediately climbing again.
* **Speed-limit awareness** — an artificial bandwidth cap is never mistaken for
  a slow server, so Smart mode does not add connections to chase a limit.

Safety
------
Segments are built once; only *concurrency* is adjusted, at natural segment
boundaries.  No download is restarted, no ``.part`` file is deleted or
invalidated, and Resume state is untouched.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024

# Minimum wall-clock time a decision must wait before another change.
_COOLDOWN = 10.0
# Back-off period after a server-imposed error.
_ERROR_COOLDOWN = 30.0
# Warm-up before any adaptive decision.
_WARMUP = 3.0
# Minimum throughput improvement that justifies adding connections.
_IMPROVEMENT = 1.2


class ConnectionGovernor:
    """Thread-safe limiter controlling how many segments run concurrently.

    Raising ``max_active`` lets more queued segments start; lowering it waits
    for active segments to finish (a natural segment boundary) before letting
    new ones in.  No in-flight segment is ever interrupted.
    """

    def __init__(self, max_active: int = 1) -> None:
        self._cond = threading.Condition()
        self._max_active = max(1, int(max_active))
        self._active = 0

    def set_max_active(self, value: int) -> None:
        with self._cond:
            self._max_active = max(1, int(value))
            self._cond.notify_all()

    @property
    def max_active(self) -> int:
        with self._cond:
            return self._max_active

    def acquire(self) -> None:
        with self._cond:
            while self._active >= self._max_active:
                self._cond.wait()
            self._active += 1

    def release(self) -> None:
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify_all()


class SmartOptimizer:
    """Initial + adaptive connection strategy for one download."""

    def __init__(
        self,
        max_connections: int = 8,
        adaptive: bool = True,
        on_status: Optional[Callable[[str], None]] = None,
        log: Optional[Callable[..., None]] = None,
    ) -> None:
        self.max_connections = max(1, int(max_connections))
        self.adaptive = bool(adaptive)
        self._on_status = on_status
        self._log = log or (lambda *a, **k: None)

        self._lock = threading.Lock()
        self._governor: Optional[ConnectionGovernor] = None
        self._segment_max = 1
        self._initial = 1
        self._start = 0.0
        self._last_change = 0.0
        self._last_error = 0.0
        self._last_obs_time = 0.0
        self._last_obs_completed = 0
        self._prev_inst = 0.0
        self._baseline = False
        self._speed_limited = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def segment_count(self, total_size: int, supports_range: bool) -> int:
        """Recommended segment count for this file (the parallelism ceiling)."""
        if not supports_range or total_size <= 0:
            return 1
        size = int(total_size)
        if size < _MB:
            base = 1
        elif size < 16 * _MB:
            base = 2
        elif size < 128 * _MB:
            base = 4
        elif size < _GB:
            base = 6
        else:
            base = self.max_connections
        return max(1, min(self.max_connections, base))

    def start(self, total_size: int, supports_range: bool) -> tuple:
        """Begin a smart download.

        Returns ``(segment_count, governor)`` — ``governor`` is ``None`` when
        adaptivity is off or the file is single-segment, in which case all
        segments run concurrently (still size-aware).
        """
        seg = self.segment_count(total_size, supports_range)
        self._segment_max = seg
        initial = seg if not self.adaptive else max(1, min(seg, 4))
        self._initial = initial
        self._start = time.monotonic()
        self._last_change = 0.0
        self._last_error = 0.0
        self._last_obs_time = 0.0
        self._last_obs_completed = 0
        self._prev_inst = 0.0
        self._baseline = False
        self._speed_limited = False
        governor = ConnectionGovernor(initial) if (self.adaptive and seg > 1) else None
        self._governor = governor
        self._emit_status(str(initial))
        self._log("Smart optimizer: initial connections = %d (max %d)", initial, seg)
        return seg, governor

    def set_speed_limited(self, limited: bool) -> None:
        """Tell the optimizer that an artificial bandwidth cap is active."""
        self._speed_limited = bool(limited)

    # ------------------------------------------------------------------ #
    # Feedback
    # ------------------------------------------------------------------ #

    def on_server_error(self, status: Optional[int]) -> None:
        """Reduce parallelism after a server-imposed failure and cool down."""
        now = time.monotonic()
        with self._lock:
            self._last_error = now
            gov = self._governor
            if gov is None:
                return
            cur = gov.max_active
            new = max(1, cur // 2)
            if new < cur:
                gov.set_max_active(new)
                self._last_change = now
                self._log(
                    "Smart optimizer: server instability detected (%s), "
                    "reducing connections %d -> %d",
                    status if status is not None else "connection error",
                    cur,
                    new,
                )
                self._emit_status(f"{cur}->{new}")

    def observe(self, completed: int, total: int) -> None:
        """Adaptive decision hook — called from the (throttled) progress path."""
        if self._governor is None:
            return
        now = time.monotonic()
        with self._lock:
            if now - self._last_error < _ERROR_COOLDOWN:
                return
            if now - self._start < _WARMUP:
                return
            if completed <= 0 or total <= 0:
                return
            # An artificial speed limit must never look like a slow server.
            if self._speed_limited:
                return
            remaining = total - completed
            if remaining <= total / max(1, self._segment_max):
                return  # near completion — be conservative

            delta_t = now - self._last_obs_time
            if delta_t < 1.0:
                return  # sample at ~1 Hz
            delta_b = completed - self._last_obs_completed
            self._last_obs_time = now
            self._last_obs_completed = completed
            inst = delta_b / delta_t if delta_t > 0 else 0.0

            if not self._baseline:
                self._baseline = True
                self._prev_inst = inst
                return

            cur = self._governor.max_active
            if cur >= self._segment_max:
                return
            if now - self._last_change < _COOLDOWN:
                return

            improved = inst >= self._prev_inst * _IMPROVEMENT
            if improved:
                new = min(self._segment_max, cur + 2)
                self._governor.set_max_active(new)
                self._last_change = now
                self._prev_inst = inst
                self._log(
                    "Smart optimizer: increased connections %d -> %d", cur, new
                )
                self._emit_status(f"{cur}->{new}")
            else:
                self._prev_inst = inst

    def _emit_status(self, text: str) -> None:
        if self._on_status:
            try:
                self._on_status(text)
            except Exception:
                pass
