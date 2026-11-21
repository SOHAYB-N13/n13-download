"""Deterministic tests for the throughput estimator.

Every test drives an injected clock, so nothing here sleeps and nothing depends
on how fast the machine is.  These are the guarantees the UI relies on:

* the displayed rate equals the bytes that actually moved, within one window;
* it falls to exactly zero when nothing arrives;
* it recovers immediately when bytes resume;
* the lifetime average excludes idle time.
"""

from __future__ import annotations

import statistics
import unittest

from core.progress import ProgressLedger
from core.speed import IDLE_GRACE, RateEstimator, SpeedTracker


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


MB = 1024 * 1024


class RateEstimatorTest(unittest.TestCase):
    def make(self, **kwargs):
        clock = FakeClock()
        kwargs.setdefault("sample_interval", 0.2)
        return RateEstimator(clock=clock, **kwargs), clock

    def feed(self, est: RateEstimator, clock: FakeClock, bps: float,
             seconds: float, step: float = 0.05) -> None:
        """Deliver ``bps`` bytes/second for ``seconds``, in ``step`` slices."""
        elapsed = 0.0
        while elapsed < seconds - 1e-9:
            clock.advance(step)
            est.add(int(bps * step))
            elapsed += step

    # -- accuracy -------------------------------------------------------

    def test_reports_the_rate_that_actually_moved(self):
        est, clock = self.make()
        self.feed(est, clock, 4 * MB, 3.0)
        snap = est.snapshot()
        self.assertAlmostEqual(snap.fast / MB, 4.0, delta=0.2)
        self.assertAlmostEqual(snap.slow / MB, 4.0, delta=0.2)

    def test_accuracy_is_independent_of_callback_frequency(self):
        """The old code's 50 ms gate made the answer depend on callback rate."""
        rates = []
        for step in (0.01, 0.05, 0.2):
            est, clock = self.make()
            self.feed(est, clock, 3 * MB, 3.0, step=step)
            rates.append(est.snapshot().fast / MB)
        for value in rates:
            self.assertAlmostEqual(value, 3.0, delta=0.3)

    def test_first_window_is_not_biased_low(self):
        """No cold-start ramp from zero: one full window is enough."""
        est, clock = self.make()
        self.feed(est, clock, 5 * MB, 1.0)
        self.assertAlmostEqual(est.snapshot().fast / MB, 5.0, delta=0.6)

    def test_tracks_a_step_change_within_a_window(self):
        est, clock = self.make()
        self.feed(est, clock, 2 * MB, 3.0)
        self.feed(est, clock, 8 * MB, 1.8)      # let the 1 s window turn over
        self.assertAlmostEqual(est.snapshot().fast / MB, 8.0, delta=0.5)

    def test_a_single_empty_tick_does_not_drag_the_readout(self):
        """An empty tick lowers the honest one-second average, not to zero.

        It does lower it: the window is weighted by time, so 0.2 s of the last
        1.2 s carrying nothing is 1/6 off the average.  What must not happen is
        the readout collapsing, which is what the old code did.
        """
        est, clock = self.make()
        self.feed(est, clock, 4 * MB, 2.0)
        before = est.snapshot().fast
        clock.advance(0.2)          # one grid tick with no bytes
        est.snapshot()
        after = est.snapshot().fast
        self.assertGreater(after, before * 0.7)
        self.assertLess(after, before)

    def test_quanta_larger_than_the_grid_do_not_inflate_the_rate(self):
        """A slow link delivers whole write buffers, so most ticks are empty.

        Averaging only the non-empty ticks reported the quantum's rate rather
        than the link's: 0.5 MB every 0.5 s on a 0.2 s grid read as ~2.5 MB/s
        instead of 1 MB/s.  This is the shape real traffic has (measured on a
        1 MB/s link, see the overhaul report 4.8); loopback never shows it
        because there every tick carries bytes.

        A one-second window over 0.5 MB quanta cannot be phase-independent, so
        the assertion is that the readings are *unbiased* — the mean over both
        phases is the true rate — and that no single reading doubles it.
        """
        est, clock = self.make()
        quantum = MB // 2
        readings = []
        for _ in range(12):                 # 0.5 MB every 0.5 s = 1 MB/s
            clock.advance(0.3)
            est.add(quantum)
            readings.append(est.snapshot().fast / MB)   # just after a quantum
            clock.advance(0.2)
            readings.append(est.snapshot().fast / MB)   # mid-gap
        steady = readings[4:]
        self.assertAlmostEqual(statistics.mean(steady), 1.0, delta=0.2)
        self.assertLess(max(steady), 1.8)
        self.assertGreater(min(steady), 0.2)

    def test_irregular_arrival_is_averaged_over_time_not_over_ticks(self):
        """Bursts and gaps must average to the true mean, not to the burst."""
        est, clock = self.make()
        # 1 MB every second, arriving in one lump, for 6 s.
        for _ in range(6):
            clock.advance(1.0)
            est.add(MB)
            est.snapshot()
        self.assertAlmostEqual(est.snapshot().fast / MB, 1.0, delta=0.2)

    def test_idle_reads_before_the_first_byte_do_not_erase_the_window(self):
        """Regression: the readout sat at 0.00 for a second after a slow start.

        Reads that arrive before any bytes at all run the idle branch (there is
        no activity yet).  Clearing the mark history there left the first tick
        with no point at or before the window start, so it measured "bytes since
        the first surviving mark" — zero — while 0.5 MB sat inside the window.
        Observed on a real 1 MB/s link: six consecutive 100 ms samples at 0.00
        while 405 KB/s was arriving.
        """
        est, clock = self.make()
        for _ in range(5):              # 1 s of reads with nothing arriving
            clock.advance(0.2)
            self.assertEqual(est.snapshot().fast, 0.0)
        clock.advance(0.2)
        est.add(MB // 2)                # the first quantum finally lands
        snap = est.snapshot()
        self.assertGreater(snap.fast, 0.0,
                           "the bytes inside the window were discarded")

    def test_a_stall_still_reads_zero_and_stays_zero(self):
        """Keeping the curve must not resurrect a rate during a real stall."""
        est, clock = self.make()
        self.feed(est, clock, 4 * MB, 2.0)
        clock.advance(IDLE_GRACE + 0.05)
        self.assertEqual(est.snapshot().fast, 0.0)
        for _ in range(10):             # reads during the stall stay at zero
            clock.advance(0.2)
            self.assertEqual(est.snapshot().fast, 0.0)


    def test_bursty_low_rate_does_not_flash_zero_between_quanta(self):
        """The zero grace must follow the link's delivery cadence.

        Eight concurrent transfers on this host delivered ~0.16 MB/s each:
        half-megabyte write-buffer quanta several seconds apart.  A fixed
        1.2 s grace called that a stall 0-6 times per transfer even though the
        next quantum was already in flight.
        """
        est, clock = self.make()
        quantum = MB // 2
        for _ in range(3):                  # 0.5 MB every 2.5 s = 0.2 MB/s
            clock.advance(2.5)
            est.add(quantum)
            est.snapshot()
        clock.advance(2.0)                  # waiting for the next quantum
        snap = est.snapshot()
        self.assertGreater(snap.fast, 0.0,
                           "a slow but active link was reported as idle")
        self.assertLess(snap.fast / MB, 0.5,
                        "the stretched window must not inflate the rate")

    def test_adaptive_grace_still_reaches_zero_on_a_real_stall(self):
        """A stretched window is bounded; a dead socket still reads zero."""
        est, clock = self.make()
        quantum = MB // 2
        for _ in range(4):
            clock.advance(2.5)
            est.add(quantum)
            est.snapshot()
        clock.advance(6.0)                  # beyond the capped 8 s window * 1.2
        self.assertEqual(est.snapshot().fast, 0.0)
        for _ in range(5):
            clock.advance(0.5)
            self.assertEqual(est.snapshot().fast, 0.0)

    def test_falls_to_exactly_zero_when_bytes_stop(self):
        est, clock = self.make()
        self.feed(est, clock, 4 * MB, 2.0)
        self.assertGreater(est.snapshot().fast, 0)
        clock.advance(IDLE_GRACE + 0.01)
        snap = est.snapshot()
        self.assertEqual(snap.fast, 0.0)
        self.assertEqual(snap.slow, 0.0)
        self.assertEqual(snap.instant, 0.0)
        self.assertTrue(snap.is_idle)

    def test_does_not_flash_zero_for_a_short_gap(self):
        est, clock = self.make()
        self.feed(est, clock, 4 * MB, 2.0)
        clock.advance(IDLE_GRACE * 0.5)
        self.assertGreater(est.snapshot().fast, 0)

    def test_recovers_immediately_after_a_stall(self):
        est, clock = self.make()
        self.feed(est, clock, 4 * MB, 2.0)
        clock.advance(5.0)
        self.assertEqual(est.snapshot().fast, 0.0)
        self.feed(est, clock, 6 * MB, 1.0)
        self.assertAlmostEqual(est.snapshot().fast / MB, 6.0, delta=1.0)

    def test_reading_is_what_makes_it_decay(self):
        """No writer calls at all: a snapshot alone must still show zero."""
        est, clock = self.make()
        self.feed(est, clock, 4 * MB, 2.0)
        clock.advance(10.0)
        self.assertEqual(est.snapshot().fast, 0.0)

    # -- lifetime ------------------------------------------------------

    def test_lifetime_average_excludes_idle_time(self):
        est, clock = self.make()
        self.feed(est, clock, 2 * MB, 2.0)      # 4 MB moved in 2 s of transfer
        clock.advance(8.0)                      # long stall, must not count
        snap = est.snapshot()
        self.assertAlmostEqual(snap.lifetime / MB, 2.0, delta=0.3)
        # Integer slices truncate by a few bytes; that is not a defect.
        self.assertAlmostEqual(snap.bytes_total / MB, 4.0, delta=0.01)

    def test_seeded_bytes_count_toward_total_but_not_the_rate(self):
        est, clock = self.make()
        est.seed(10 * MB)
        self.assertEqual(est.snapshot().bytes_total, 10 * MB)
        self.assertEqual(est.snapshot().fast, 0.0)
        self.feed(est, clock, 4 * MB, 1.0)
        self.assertAlmostEqual(est.snapshot().fast / MB, 4.0, delta=0.6)

    def test_snapshot_is_a_consistent_reading(self):
        est, clock = self.make()
        self.feed(est, clock, 3 * MB, 2.0)
        snap = est.snapshot()
        self.assertAlmostEqual(snap.bytes_total / MB, 6.0, delta=0.01)
        self.assertGreater(snap.best_estimate, 0)

    # -- a transfer that has not completed its first tick yet --------------

    def test_pending_bytes_are_never_discarded_as_idle(self):
        """Regression: the readout stuck at 0.00 while megabytes were arriving.

        Activity used to mean "a grid tick completed".  A transfer whose first
        ticks landed just before the tick boundary was therefore judged idle,
        and the idle branch discarded the pending bytes — so the display read
        0.00 for over a second of a perfectly healthy download (observed
        intermittently in the benchmark: median error 92–100 % on runs where it
        happened, 2–6 % otherwise).
        """
        est, clock = self.make()
        clock.advance(0.05)
        est.add(MB)                     # no tick is due yet
        snap = est.snapshot()           # a read must not throw the byte away
        self.assertEqual(snap.bytes_total, MB)
        self.assertEqual(snap.idle_for, 0.0,
                         "pending bytes were reported as idle time")

        clock.advance(0.2)              # the tick is now due
        snap = est.snapshot()
        self.assertGreater(snap.fast, 0.0, "pending bytes were lost")
        # 1 MB over the 0.25 s since the last tick.
        self.assertAlmostEqual(snap.fast / MB, 4.0, delta=0.3)

    def test_a_read_before_the_first_tick_does_not_zero_the_rate(self):
        est, clock = self.make()
        # Reads at 100 ms cadence, bytes every 50 ms: the first read happens
        # before the first 200 ms tick and must not report a permanent zero.
        for _ in range(3):
            clock.advance(0.05)
            est.add(MB // 4)
            clock.advance(0.05)
            snap = est.snapshot()
            self.assertEqual(snap.bytes_total, est.bytes_total)
        clock.advance(0.25)
        self.assertGreater(est.snapshot().fast, 0.0)


class SpeedTrackerCompatTest(unittest.TestCase):
    """The CLI progress bar still reads ``formatted_speed``."""

    def test_tracker_exposes_the_legacy_surface(self):
        clock = FakeClock()
        tracker = SpeedTracker(window_size=20, sample_interval=0.2)
        tracker._rate = RateEstimator(clock=clock)
        for _ in range(40):
            clock.advance(0.05)
            tracker.add(int(2 * MB * 0.05))
        self.assertGreater(tracker.average_speed, 0)
        self.assertTrue(tracker.formatted_speed.endswith("/s"))
        self.assertAlmostEqual(tracker.bytes_downloaded / MB, 4.0, delta=0.01)

    def test_seed_suppresses_the_resume_spike(self):
        tracker = SpeedTracker()
        tracker.seed(100 * MB)
        tracker.add(1024)
        self.assertLess(tracker.average_speed, 1024 * 10)


class ProgressLedgerTest(unittest.TestCase):
    def test_delivers_in_order_and_never_backwards(self):
        seen = []
        ledger = ProgressLedger(total=10, callback=lambda v, t: seen.append(v),
                               interval=0.0, byte_threshold=1)
        for value in (1, 2, 3, 2, 5, 4, 7):
            ledger.raise_to(value)
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(seen[-1], 7)

    def test_coalesces_small_increments(self):
        seen = []
        ledger = ProgressLedger(total=10 * MB, callback=lambda v, t: seen.append(v),
                               interval=10.0, byte_threshold=1024 * 1024)
        for _ in range(100):
            ledger.add(1024)          # 100 KB total, below the threshold
        self.assertEqual(seen, [])
        ledger.add(2 * MB)
        self.assertEqual(seen, [2 * MB + 100 * 1024])

    def test_reset_is_the_only_way_backwards(self):
        seen = []
        ledger = ProgressLedger(total=10, callback=lambda v, t: seen.append(v),
                               interval=0.0, byte_threshold=1)
        ledger.add(5)
        ledger.reset(0)
        self.assertEqual(seen, [5, 0])
        self.assertEqual(ledger.completed, 0)

    def test_a_raising_callback_cannot_break_the_transfer(self):
        def boom(_value, _total):
            raise RuntimeError("sink is broken")

        ledger = ProgressLedger(total=10, callback=boom, interval=0.0, byte_threshold=1)
        ledger.add(5)             # must not propagate
        self.assertEqual(ledger.completed, 5)


if __name__ == "__main__":
    unittest.main()
