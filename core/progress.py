"""Monotonic aggregate progress for one download.

The problem this solves
=======================
Every part thread used to do this:

1. read the shared byte counter under ``progress_lock``,
2. release the lock,
3. call the progress callback with the value it had read.

Two threads could therefore deliver 11 MB and then 10 MB, because the second
one had read the counter earlier and lost the race to the callback.  The sink
(``DownloadTask.update_speed``) saw a *decrease*, interpreted it as "the
download restarted", and reset the displayed speed and the lifetime average.
That is a real cause of jitter in both the progress bar and the speed readout,
and it is invisible in single-part tests.

This ledger makes the counter and its delivery one object:

*   ``add()`` is the only writer and is monotonic by construction.
*   ``report()`` serialises delivery on a dedicated lock, so values reach the
    sink in non-decreasing order — the sink never sees a phantom restart.
*   ``reset()`` is the only way the counter may go backwards, and it is
    explicit (used when the engine genuinely restarts a download as a single
    stream).
*   Delivery is coalesced by time and by byte threshold, so the sink is not
    called once per read slice on a fast link.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class ProgressLedger:
    """Thread-safe, monotonic progress accumulator with coalesced delivery."""

    def __init__(
        self,
        total: int = 0,
        initial: int = 0,
        callback: Optional[Callable[[int, int], None]] = None,
        interval: float = 0.05,
        byte_threshold: int = 256 * 1024,
    ) -> None:
        self._lock = threading.Lock()
        # Held across the callback so deliveries cannot overtake each other.
        # Never taken by add(), so writers are unaffected by a slow sink.
        self._deliver_lock = threading.Lock()
        self._total = max(0, int(total))
        self._written = max(0, int(initial))
        self._delivered = self._written
        self._callback = callback
        self._interval = max(0.0, float(interval))
        self._byte_threshold = max(1, int(byte_threshold))
        # Start the clock now so the *first* delivery also respects the
        # coalescing interval.  Callers that need the initial value published
        # immediately (the engine does) call report(force=True).
        self._last_delivery = time.monotonic()
        self._delivered_bytes = 0

    # ------------------------------------------------------------------ #
    # Writer side
    # ------------------------------------------------------------------ #

    def add(self, byte_count: int) -> int:
        """Add successfully written bytes and report them if due."""
        if byte_count <= 0:
            return self.completed
        with self._lock:
            self._written += byte_count
            value = self._written
        self.report()
        return value

    def set_total(self, total: int) -> None:
        """Record the expected size once it is known."""
        with self._lock:
            self._total = max(0, int(total))

    def set_callback(self, callback: Optional[Callable[[int, int], None]]) -> None:
        with self._lock:
            self._callback = callback

    def reset(self, value: int = 0, total: Optional[int] = None) -> None:
        """Restart the counter — the only legal way for progress to go back.

        Delivers the reset value immediately so the sink cannot keep showing
        the pre-restart position.
        """
        with self._lock:
            self._written = max(0, int(value))
            self._delivered = self._written
            if total is not None:
                self._total = max(0, int(total))
            self._delivered_bytes = self._written
            self._last_delivery = 0.0
        self.report(force=True)

    # ------------------------------------------------------------------ #
    # Reader side
    # ------------------------------------------------------------------ #

    @property
    def completed(self) -> int:
        with self._lock:
            return self._written

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self._total - self._written)

    def raise_to(self, value: int) -> None:
        """Lift the counter to ``value`` if it is behind (never lower it).

        Used to reconcile against the bytes actually on disk after an attempt
        fails: the write buffer can make the counter lag the files, and the
        displayed position must never sit below what has really been stored.
        """
        with self._lock:
            if value <= self._written:
                return
            self._written = int(value)
        self.report()

    def report(self, force: bool = False) -> None:
        """Deliver the current value if it is new enough to be worth sending."""
        with self._deliver_lock:
            now = time.monotonic()
            with self._lock:
                value = self._written
                total = self._total
                if not force:
                    if value <= self._delivered:
                        return
                    if (value - self._delivered_bytes) < self._byte_threshold and (
                        now - self._last_delivery
                    ) < self._interval:
                        return
                self._delivered = value
                self._delivered_bytes = value
                self._last_delivery = now
                callback = self._callback
            if callback is None:
                return
            try:
                callback(value, total)
            except Exception:
                # A failing listener must never break a transfer; the engine
                # logs it where it can, and the next tick will try again.
                pass
