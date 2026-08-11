"""Shared bandwidth limiter.

When ``max_speed_bps`` is configured, every download thread calls
:meth:`BandwidthLimiter.consume` before writing a chunk.  The limiter uses a
token-bucket scheme with short sleeps so threads self-throttle to the global
cap regardless of how many connections are active.

A per-call minimum keeps tiny chunks from busy-looping when the bucket is
empty, and a soft burst equal to one second of traffic allows brief spikes
(typical of TCP ramp-up) to pass without stalling.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class BandwidthLimiter:
    """Thread-safe global rate limiter (0 = unlimited)."""

    def __init__(self, max_bytes_per_second: int = 0):
        self._lock = threading.Lock()
        self._max_rate = max(0, int(max_bytes_per_second))
        # Allow up to one second of burst before throttling kicks in.
        self._burst = self._max_rate
        self._tokens = float(self._burst)
        self._last_refill = time.monotonic()

    @property
    def enabled(self) -> bool:
        return self._max_rate > 0

    @property
    def max_rate(self) -> int:
        return self._max_rate

    def update_limit(self, max_bytes_per_second: int) -> None:
        """Change the cap at runtime."""
        with self._lock:
            self._max_rate = max(0, int(max_bytes_per_second))
            self._burst = self._max_rate or self._burst
            self._tokens = min(self._tokens, float(self._burst)) if self._burst else self._tokens

    def consume(self, amount: int, should_stop: "Optional[Callable[[], bool]]" = None) -> None:
        """Block until ``amount`` bytes are allowed under the cap.

        No-op when the limiter is disabled, so the hot download loop pays only
        a single attribute read in the common unlimited case.

        ``should_stop`` (optional) is polled during the sleep so a pause/cancel
        request can abort the wait promptly — otherwise a long throttle sleep
        would delay cancellation and app shutdown.

        Algorithm
        ---------
        1. Acquire the lock and refill the token bucket from elapsed time.
        2. Deduct as many tokens as available; record how many bytes still
           need to be waited for (``deficit``).
        3. Release the lock *before* sleeping so other threads can refill.
        4. Sleep in short slices only when there is an actual deficit (> 0).
        """
        if not self._max_rate or amount <= 0:
            return

        deficit = 0.0
        with self._lock:
            self._refill_locked()
            if self._tokens >= amount:
                # Fast path: bucket has enough tokens — no sleep needed.
                self._tokens -= amount
            else:
                # Consume all available tokens; sleep for the remainder.
                deficit = amount - self._tokens
                self._tokens = 0.0

        # Sleep outside the lock so other threads can progress concurrently.
        # Re-check _max_rate in case update_limit() raced after lock release.
        if deficit > 0 and self._max_rate:
            remaining = deficit / self._max_rate
            deadline = time.monotonic() + remaining
            while True:
                if should_stop is not None and should_stop():
                    return
                left = deadline - time.monotonic()
                if left <= 0:
                    return
                time.sleep(min(0.1, left))

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._last_refill = now
        self._tokens = min(float(self._burst), self._tokens + elapsed * self._max_rate)


def make_limiter(config) -> Optional[BandwidthLimiter]:
    """Factory: returns a limiter only when a positive cap is configured."""
    cap = getattr(config, "max_speed_bps", 0) or 0
    if cap <= 0:
        return None
    return BandwidthLimiter(cap)


# Module-level singleton shared across all download threads so bandwidth
# shaping applies globally even when many concurrent tasks are running.
_global_limiter: Optional[BandwidthLimiter] = None
_global_limiter_lock = threading.Lock()

# Optional override applied by the scheduler (night speed cap).  ``None`` means
# "use the configured max_speed_bps"; any integer temporarily replaces it
# without mutating the user's setting.
_scheduled_override: Optional[int] = None


def set_schedule_override(bps: Optional[int]) -> None:
    """Set/clear the scheduler's temporary bandwidth cap.

    Pass ``None`` to clear the override and fall back to ``max_speed_bps``.
    """
    global _scheduled_override
    _scheduled_override = None if bps is None else max(0, int(bps))


def sync_limiter_from_config(config) -> None:
    """Update (or create) the global limiter from the current config.

    Called by LegacyDownloadRunner before each download so that settings
    changes made in the UI take effect immediately without restarting.
    The scheduler override, when active, takes precedence over the configured
    cap so the night-speed rule survives per-download re-syncs.
    """
    global _global_limiter
    cap = getattr(config, "max_speed_bps", 0) or 0
    if _scheduled_override is not None:
        cap = _scheduled_override
    with _global_limiter_lock:
        if cap <= 0:
            _global_limiter = None
        elif _global_limiter is None:
            _global_limiter = BandwidthLimiter(cap)
        else:
            _global_limiter.update_limit(cap)


def get_global_limiter() -> Optional[BandwidthLimiter]:
    """Return the current global limiter instance (may be None)."""
    with _global_limiter_lock:
        return _global_limiter
