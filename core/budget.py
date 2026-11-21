"""Process-wide cap on simultaneous transfer connections.

Why a global cap
================
Per-download concurrency is bounded by the optimizer's ceiling, and the queue
bounds how many downloads run at once — but the product of the two is not
bounded by anything.  At the defaults (3 downloads × 8 connections) that is 24
sockets; at the maximum the settings allow (10 × 8) it is 80, each with a 4 MB
``SO_RCVBUF``: roughly 320 MB of kernel buffers on a machine that may have 4 GB
of RAM in total.

This module makes the total explicit and small.  It is deliberately a plain
semaphore rather than a fair-share scheduler: per-download concurrency is
already governed per download, and the queue decides which downloads run.  What
was missing was a hard ceiling on the sum, and that is all this adds.

The limit is never allowed below one download's own ceiling, so a single
download can always make progress even if it is the only one running.
"""

from __future__ import annotations

import threading
from typing import Optional

#: Default total across the whole process.
DEFAULT_TOTAL = 32

_global_budget: Optional["ConnectionBudget"] = None
_global_lock = threading.Lock()


class ConnectionBudget:
    """A counting semaphore with a human-readable limit."""

    def __init__(self, total: int = DEFAULT_TOTAL) -> None:
        self._total = max(1, int(total))
        self._sem = threading.BoundedSemaphore(self._total)
        # Downloads currently transferring, for the fair-share cap below.
        self._tasks = 0
        self._tasks_lock = threading.Lock()
        # Genuine client-side occupancy, not a policy ceiling.  ``_peak_active``
        # answers "how many budget slots were actually held at once" — the
        # question the old server-side ``peak_conns`` counter pretended to answer.
        self._active = 0
        self._peak_active = 0
        self._slots_lock = threading.Lock()

    @property
    def total(self) -> int:
        return self._total

    # -- fair share -----------------------------------------------------

    def enter_task(self) -> int:
        """Register a transfer that is about to compete for slots."""
        with self._tasks_lock:
            self._tasks += 1
            return self._tasks

    def exit_task(self) -> None:
        with self._tasks_lock:
            self._tasks = max(0, self._tasks - 1)

    def fair_share(self) -> int:
        """Connections this download may hold without starving its peers.

        ``0`` means "no cap": one download should be free to use the whole
        budget.  With N downloads the cap is ``total // N``, which turns a race
        for slots into a guaranteed floor for each one.
        """
        with self._tasks_lock:
            tasks = self._tasks
        if tasks <= 1:
            return 0
        return max(1, self._total // tasks)

    @property
    def peak_active(self) -> int:
        """Maximum number of budget slots held at the same time."""
        with self._slots_lock:
            return self._peak_active

    def acquire(self) -> bool:
        """Take one slot, blocking until one is free."""
        self._sem.acquire()
        with self._slots_lock:
            self._active += 1
            if self._active > self._peak_active:
                self._peak_active = self._active
        return True

    def release(self) -> None:
        with self._slots_lock:
            if self._active > 0:
                self._active -= 1
        try:
            self._sem.release()
        except ValueError:
            # Releasing more than was acquired is a programming error, but it
            # must never take a download down with it.
            pass


class ConnectionMeter:
    """Per-download counter of simultaneously open transfer requests.

    Increment after a budget slot is acquired and decrement immediately before
    it is released.  Unlike a server-side accepted-socket counter this cannot
    include handlers the client has already abandoned, and unlike the optimizer
    ceiling it measures what actually happened.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self._peak = 0

    def enter(self) -> None:
        with self._lock:
            self._active += 1
            if self._active > self._peak:
                self._peak = self._active

    def exit(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)

    @property
    def peak(self) -> int:
        with self._lock:
            return self._peak


def sync_connection_budget(config) -> ConnectionBudget:
    """Create or resize the global budget from configuration.

    Resizing replaces the semaphore; connections already holding a slot from
    the old one keep it and release into the new one, which can only ever make
    the effective limit slightly more permissive for the duration of those
    transfers.  That is preferable to blocking every caller on a rebuild.
    """
    global _global_budget
    try:
        total = int(getattr(config, "max_total_connections", DEFAULT_TOTAL) or DEFAULT_TOTAL)
    except (TypeError, ValueError):
        total = DEFAULT_TOTAL
    # A single download must always be able to reach its own ceiling.
    floor = int(getattr(config, "smart_max_connections", 8) or 8)
    total = max(floor, min(256, total))
    with _global_lock:
        if _global_budget is None or _global_budget.total != total:
            _global_budget = ConnectionBudget(total)
        return _global_budget


def get_connection_budget() -> ConnectionBudget:
    global _global_budget
    with _global_lock:
        if _global_budget is None:
            _global_budget = ConnectionBudget(DEFAULT_TOTAL)
        return _global_budget
