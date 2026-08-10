"""Download speed tracking.

A rolling window of instantaneous samples feeds the average-speed readout.
The hot path (:meth:`add`) is called once per chunk from every worker thread,
so it is kept as cheap as possible: an atomic accumulation under a lock plus
sampling only when enough wall-clock time has elapsed.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from core.utils import format_speed


class SpeedTracker:
    """Thread-safe rolling-average speed tracker."""

    def __init__(self, window_size: int = 20, sample_interval: float = 0.2):
        self.lock = threading.Lock()
        self.window_size = max(1, window_size)
        self.sample_interval = max(0.01, sample_interval)
        self._speed_sum = 0.0
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self._bytes_downloaded = 0
            self._start_time = time.monotonic()
            self._last_sample_time = self._start_time
            self._last_sample_bytes = 0
            self._speeds: deque[float] = deque(maxlen=self.window_size)
            self._speed_sum = 0.0
            self._cached_avg = 0.0

    def add(self, bytes_count: int) -> None:
        """Record ``bytes_count`` bytes; sample the instantaneous speed.

        Uses a running sum to avoid O(window_size) summation on every sample.
        The lock is acquired once per call; the critical section is kept as
        lean as possible for the multi-threaded hot path.
        """
        with self.lock:
            self._bytes_downloaded += bytes_count
            now = time.monotonic()
            elapsed = now - self._last_sample_time
            if elapsed >= self.sample_interval:
                delta_bytes = self._bytes_downloaded - self._last_sample_bytes
                instant_speed = delta_bytes / elapsed if elapsed > 0 else 0.0
                if len(self._speeds) == self.window_size:
                    self._speed_sum -= self._speeds[0]
                self._speeds.append(instant_speed)
                self._speed_sum += instant_speed
                self._last_sample_bytes = self._bytes_downloaded
                self._last_sample_time = now
                self._cached_avg = (
                    self._speed_sum / len(self._speeds) if self._speeds else 0.0
                )

    def seed(self, already_bytes: int) -> None:
        """Pretend ``already_bytes`` were transferred at reset time.

        Used on resume so the first sample does not over-report speed.
        """
        with self.lock:
            self._bytes_downloaded = already_bytes
            self._last_sample_bytes = already_bytes

    @property
    def bytes_downloaded(self) -> int:
        with self.lock:
            return self._bytes_downloaded

    @property
    def elapsed(self) -> float:
        with self.lock:
            return max(0.0, time.monotonic() - self._start_time)

    @property
    def average_speed(self) -> float:
        with self.lock:
            return self._cached_avg

    @property
    def formatted_speed(self) -> str:
        return format_speed(self.average_speed)
