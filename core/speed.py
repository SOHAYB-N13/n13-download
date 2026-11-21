"""Download rate estimation.

Why this exists
===============
The previous implementation sampled speed from inside the byte counter: a
sample was taken only when a chunk happened to be recorded, and only if at
least 50 ms had passed since the last sample *attempt*.  With eight parts
reporting progress ~60 times a second that gate almost never opened, so the
number on screen was a stale sample captured during a pause — measured 7.5×
below the true rate (see docs/DOWNLOAD_ENGINE_PERFORMANCE_OVERHAUL.md §4).

Worse, a rate can only be *observed* when bytes arrive.  Sampling exclusively
on write therefore makes a stalled transfer look busy forever: the last
non-zero value is simply never replaced.

This module inverts both decisions:

*   **Fixed sampling grid.**  Bytes accumulate into a pending counter and are
    converted into a rate once per ``sample_interval``, using the *elapsed
    time* rather than the number of calls.  Callback frequency no longer
    affects the result.
*   **Evaluation on read.**  :meth:`RateEstimator.snapshot` advances the grid
    itself, so an idle transfer produces zero-valued samples with no writer
    activity at all.  A readout can therefore never be older than one grid
    tick plus one idle grace period.

Three rates are exposed, each with a defined meaning:

``instant``
    The last grid tick only.  Diagnostic; too noisy to display.
``fast``
    Mean of the last :data:`FAST_WINDOW` grid ticks — one second of data at
    the default grid.  This is "current speed": it converges within a second,
    is not thrown off by a single slow tick, and is exactly the "average over
    the last second" a user intuitively compares against.
``slow``
    Exponentially weighted, ~5 s time constant.  Used for ETA, where a stable
    denominator matters more than responsiveness.
``lifetime``
    Total bytes divided by the time spent *actually transferring*.  Pauses and
    stalls are excluded, so a resumed download does not report a deflated
    average for the rest of its life.

The EWMA coefficient is derived from elapsed time (``1 - exp(-dt/tau)``) rather
than being a fixed per-call constant, so the smoothing behaviour is the same at
any sampling rate.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

from core.utils import format_speed

#: Grid tick.  Bytes are turned into a rate this often; also the resolution of
#: every rate the UI can show.
DEFAULT_INTERVAL = 0.2

#: Grid ticks averaged into the responsive rate.  5 ticks × 0.2 s = 1 s.
FAST_WINDOW = 5

#: Time constant of the stable rate (ETA).
SLOW_TAU = 5.0

#: How long a transfer may deliver nothing before the displayed rate is forced
#: to exactly zero.  Long enough that a single delayed update or a scheduling
#: hiccup cannot flash "0 B/s", short enough that a dead connection does not
#: keep advertising throughput.
IDLE_GRACE = 1.2

#: A bursty link delivers bytes in quanta: one part flushes a write buffer,
#: then several sampling grids pass with nothing.  The responsive window grows
#: with the observed gap between bursts so the readout does not flash zero
#: while the next quantum is legitimately in flight.  The gap is capped so a
#: dead connection cannot make the display advertise stale throughput for a
#: minute; a genuine stall still reaches zero within ``MAX_WINDOW * 1.2``.
MAX_ARRIVAL_GAP = 6.0
MAX_WINDOW = 8.0


@dataclass(frozen=True)
class SpeedSnapshot:
    """One evaluation of the estimator.  All rates are bytes per second."""

    instant: float
    fast: float
    slow: float
    lifetime: float
    idle_for: float
    bytes_total: int

    @property
    def is_idle(self) -> bool:
        return self.fast <= 0.0

    @property
    def best_estimate(self) -> float:
        """The rate to use when only one number is wanted (ETA)."""
        if self.slow > 0:
            return self.slow
        return self.fast


class RateEstimator:
    """Thread-safe, time-based throughput estimator.

    ``clock`` is injectable so tests can drive it deterministically instead of
    sleeping.
    """

    def __init__(
        self,
        sample_interval: float = DEFAULT_INTERVAL,
        fast_window: int = FAST_WINDOW,
        slow_tau: float = SLOW_TAU,
        idle_grace: float = IDLE_GRACE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.Lock()
        self._interval = max(0.01, float(sample_interval))
        self._fast_window = max(1, int(fast_window))
        self._slow_tau = max(0.05, float(slow_tau))
        self._idle_grace = max(0.0, float(idle_grace))
        self._clock = clock
        self.reset()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def reset(self, already_bytes: int = 0) -> None:
        """Clear all state.  ``already_bytes`` seeds the lifetime average."""
        now = self._clock()
        with self._lock:
            self._pending = 0
            self._total = max(0, int(already_bytes))
            self._seed_bytes = max(0, int(already_bytes))
            self._instant = 0.0
            self._fast = 0.0
            self._slow = 0.0
            # (timestamp, cumulative bytes) at each grid tick.  The fast rate is
            # read off this curve at ``now - window``; see ``_fast_locked``.
            # Seeded with the starting total so the curve always has a left
            # edge: without it the first tick's window has zero span and the
            # rate reads zero until a second tick lands.
            self._marks: deque[tuple[float, int]] = deque()
            self._marks.append((now, self._total))
            self._window = self._fast_window * self._interval
            # EWMA of the gap between delivery bursts.  Short gaps (continuous
            # traffic) leave the window at its fixed one-second default; long
            # gaps stretch it so a slow link is measured over whole quanta.
            self._arrival_gap = 0.0
            self._primed = False
            self._active_seconds = 0.0
            self._last_sample = now
            self._last_activity: Optional[float] = now if already_bytes else None
            self._started = now

    # ------------------------------------------------------------------ #
    # Write path (called once per chunk from every worker thread)
    # ------------------------------------------------------------------ #

    def add(self, byte_count: int) -> None:
        """Record ``byte_count`` bytes as successfully transferred.

        Deliberately cheap: one lock acquisition and a possible grid tick.
        """
        if byte_count <= 0:
            return
        now = self._clock()
        with self._lock:
            if self._last_activity is not None:
                gap = now - self._last_activity
                # Only a *pause in delivery* is a cadence signal.  Adds from
                # several part threads normally land a few milliseconds apart;
                # feeding those into the EWMA would shrink the window on the
                # busiest links, the exact opposite of what is needed.
                if self._interval <= gap <= MAX_ARRIVAL_GAP:
                    gap = min(gap, MAX_ARRIVAL_GAP)
                    if self._arrival_gap <= 0.0:
                        self._arrival_gap = gap
                    else:
                        self._arrival_gap = min(
                            MAX_ARRIVAL_GAP,
                            self._arrival_gap * 0.7 + gap * 0.3,
                        )
            self._pending += byte_count
            self._total += byte_count
            # Activity is "bytes arrived", not "a grid tick completed".  Setting
            # this only in ``_advance_locked`` meant a transfer whose first ticks
            # landed badly was judged idle, and the idle branch in ``snapshot()``
            # then discarded the pending bytes — the readout stuck at 0.00 for
            # over a second while megabytes were arriving.
            self._last_activity = now
            self._advance_locked(now)

    def seed(self, already_bytes: int) -> None:
        """Pretend ``already_bytes`` were transferred before this session.

        Used on resume so the first sample does not report the whole partial
        file as one second of throughput.  Seeded bytes count toward the
        lifetime average but not toward the current rate.
        """
        with self._lock:
            self._total = max(0, int(already_bytes))
            self._seed_bytes = self._total
            # The curve must be rebased too: the marks from before the seed hold
            # totals that no longer relate to ``_total``, and a stale left edge
            # makes the first window read the whole partial file as throughput.
            now = self._clock()
            self._marks.clear()
            self._marks.append((now, self._total))
            self._last_sample = now

    # ------------------------------------------------------------------ #
    # Read path
    # ------------------------------------------------------------------ #

    def snapshot(self) -> SpeedSnapshot:
        """Evaluate now, advancing the sampling grid if a tick is due.

        Reading is what makes an idle transfer decay: with no writer calls the
        grid still ticks on read, producing zero-rate samples.
        """
        now = self._clock()
        with self._lock:
            self._advance_locked(now)
            idle_for = (
                math.inf if self._last_activity is None
                else max(0.0, now - self._last_activity)
            )
            # Bytes waiting for the next tick mean this transfer is active, even
            # if no tick has completed yet.  Without this guard the idle branch
            # below can run before the very first tick and throw away real bytes.
            if self._pending > 0:
                idle_for = 0.0
            # Scale the zero grace with the transfer's own delivery cadence.
            # A fixed 1.2 s grace is shorter than a legitimate 0.5 MB quantum
            # on a link shared by eight transfers, so it reported 0 B/s
            # between bursts while bytes were still inside the rate window.
            grace = max(self._idle_grace, self._window_locked() * 1.2)
            if idle_for >= grace:
                # Force exactly zero rather than an asymptotic tail: "0 B/s"
                # is the honest answer once nothing has arrived for a while.
                # The EWMA state is cleared too, so resuming starts clean.
                #
                # The mark history is deliberately *kept*.  It is a cumulative
                # curve, not a rate, and clearing it blinded the next window:
                # after a stall the first tick had no point at or before the
                # window start, so it measured "bytes since the first surviving
                # mark" and reported 0.00 for a second while half a megabyte sat
                # inside the window.  Because the grace (1.2 s) is longer than
                # the window (1.0 s), a genuine stall still reads zero here and
                # stays zero until new bytes arrive — the curve alone gives 0.
                self._instant = 0.0
                self._fast = 0.0
                self._slow = 0.0
                self._pending = 0
                self._primed = False
                # A gap longer than the adaptive grace was a stall, not a
                # delivery cadence.  Forgetting it keeps the first new bytes
                # after recovery on the responsive one-second window.
                self._arrival_gap = 0.0
                # Rebase activity so the next burst is compared with the
                # recovery point, not with the last byte before a long stall.
                self._last_activity = now
            return SpeedSnapshot(
                instant=self._instant,
                fast=self._fast,
                slow=self._slow,
                lifetime=self._lifetime_locked(),
                idle_for=idle_for,
                bytes_total=self._total,
            )

    @property
    def bytes_total(self) -> int:
        with self._lock:
            return self._total

    @property
    def active_seconds(self) -> float:
        with self._lock:
            return self._active_seconds

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _lifetime_locked(self) -> float:
        if self._active_seconds <= 0.0:
            return 0.0
        # Seeded bytes were transferred before this session, so they are not
        # divided by this session's active time.
        moved = self._total - self._seed_bytes
        return max(0.0, moved) / self._active_seconds

    def _advance_locked(self, now: float) -> None:
        """Turn pending bytes into a rate if a grid tick is due."""
        elapsed = now - self._last_sample
        if elapsed < self._interval:
            return
        moved = self._pending
        self._pending = 0
        self._last_sample = now
        rate = moved / elapsed if elapsed > 0 else 0.0
        self._instant = rate
        self._marks.append((now, self._total))
        self._fast = self._fast_locked(now, self._window_locked())
        if moved <= 0:
            # Nothing arrived in this tick.  The window already reflects it (the
            # curve is flat across the gap); only the stable estimate and the
            # active-time accounting are left alone, so one empty tick cannot
            # drag the ETA denominator down.
            return
        if not self._primed:
            # The stable rate starts at the first real measurement instead of
            # climbing out of zero for a whole time constant.
            self._slow = rate
            self._primed = True
        else:
            self._slow = self._blend(self._slow, rate, elapsed, self._slow_tau)
        self._active_seconds += elapsed
        self._last_activity = now

    def _window_locked(self) -> float:
        base = self._fast_window * self._interval
        if self._arrival_gap <= 0.0:
            return base
        return min(MAX_WINDOW, max(base, self._arrival_gap * 1.5))

    def _fast_locked(self, now: float, window: float) -> float:
        """Mean rate over the last *window* seconds, ending *now*.

        Read off the cumulative-byte curve at ``now - window`` rather than
        averaged over the last N grid ticks, because the ticks are not equally
        spaced and not equally full.  Bytes reach this module in quanta — a part
        flushes a whole write buffer at once — so on a slow link most ticks are
        empty and a handful carry a full quantum.  Averaging only the non-empty
        ones reported the quantum's rate instead of the link's: 0.5 MB every
        0.5 s on a 0.2 s grid read as ~2.5 MB/s against a true 1 MB/s, measured
        on a real 1 MB/s link (overhaul report 4.8).  Loopback never showed it
        because there every tick carries bytes.

        Anchoring the window to ``now`` also matters: ending it at the last tick
        excludes the partial interval in progress, which biased the same case
        *low* instead.
        """
        marks = self._marks
        if not marks:
            return 0.0
        target = now - window
        while len(marks) > 1 and marks[1][0] <= target:
            marks.popleft()
        first_t, first_b = marks[0]
        if first_t > target:
            # Less history than one window: report the mean since the first
            # mark so a young transfer is not biased low.
            span = now - first_t
            if span <= 0:
                return 0.0
            return max(0.0, self._total - first_b) / span
        base_b = first_b
        prev_t, prev_b = first_t, first_b
        for mark_t, mark_b in marks:
            if mark_t >= target:
                if mark_t > prev_t:
                    frac = (target - prev_t) / (mark_t - prev_t)
                    base_b = prev_b + (mark_b - prev_b) * frac
                else:
                    base_b = mark_b
                break
            prev_t, prev_b = mark_t, mark_b
        return max(0.0, self._total - base_b) / window

    @staticmethod
    def _blend(current: float, sample: float, dt: float, tau: float) -> float:
        """Time-aware exponential smoothing (same curve at any sample rate)."""
        alpha = 1.0 - math.exp(-dt / tau)
        return current + (sample - current) * alpha


class SpeedTracker:
    """CLI/console speed readout, backed by :class:`RateEstimator`.

    Kept as a separate name because ``ui/progress.py`` renders it directly and
    the rich progress bar expects a cheap ``formatted_speed`` string.
    """

    def __init__(self, window_size: int = 20, sample_interval: float = 0.2):
        # ``window_size`` is accepted for backward compatibility with the old
        # constructor; the estimator is time-based and does not need a window
        # length.
        self._rate = RateEstimator(sample_interval=sample_interval)

    def reset(self) -> None:
        self._rate.reset()

    def add(self, bytes_count: int) -> None:
        self._rate.add(bytes_count)

    def seed(self, already_bytes: int) -> None:
        self._rate.seed(already_bytes)

    @property
    def bytes_downloaded(self) -> int:
        return self._rate.bytes_total

    @property
    def elapsed(self) -> float:
        return self._rate.active_seconds

    @property
    def average_speed(self) -> float:
        return self._rate.snapshot().fast

    @property
    def formatted_speed(self) -> str:
        return format_speed(self.average_speed)
