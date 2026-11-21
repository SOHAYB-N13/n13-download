"""Smart Download Optimizer — behaviour validation.

These tests drive the controller with a fake clock and synthetic byte counts, so
they assert the *policy* (when does concurrency grow, when does it stop) rather
than the shape of its internal variables.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.control import TaskControl
from core.download import DownloadController
from core.optimizer import (
    AUTO_POLICY,
    BALANCED_POLICY,
    CONSERVATIVE_POLICY,
    GAIN_THRESHOLD,
    HIGH_PERFORMANCE_POLICY,
    MODES,
    TARGET_SEGMENT,
    ConnectionGovernor,
    SmartOptimizer,
    is_adaptive,
    policy_for,
)
from core.session import SessionManager
from tests.helpers import (
    DATA,
    NoRangeHandler,
    RangeHandler,
    TestServer,
    TrueNoRangeHandler,
)

_MB = 1024 * 1024


def _smart_cfg(max_connections: int = 8, adaptive: bool = True) -> AppConfig:
    cfg = AppConfig()
    cfg.block_private_urls = False
    cfg.connection_mode = "smart"
    cfg.smart_max_connections = max_connections
    cfg.smart_adaptive = adaptive
    cfg.verify_size = True
    cfg.max_retries = 3
    cfg.retry_delay = 0.01
    cfg.retry_max_delay = 0.2
    return cfg


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def drive(opt: SmartOptimizer, clock: FakeClock, bps_per_conn: float,
          conns: int, seconds: float, step: float = 0.5) -> int:
    """Run ``observe`` at a fixed per-connection rate for ``seconds``.

    ``completed`` is derived from the rate so the controller sees a throughput
    consistent with the connection count it has allowed.
    """
    completed = 1
    elapsed = 0.0
    total = 1000 * _MB
    while elapsed < seconds - 1e-9:
        clock.advance(step)
        elapsed += step
        completed += int(bps_per_conn * min(conns, opt._governor.max_active) * step)
        opt.observe(completed, total)
    return opt._governor.max_active


class SegmentPlanTest(unittest.TestCase):
    """Ranges are parallelism headroom, not the connection count."""

    def test_unknown_or_unrangeable_is_single_stream(self):
        o = SmartOptimizer(max_connections=8)
        self.assertEqual(o.segment_count(0, True), 1)
        self.assertEqual(o.segment_count(500 * _MB, False), 1)

    def test_small_files_are_not_chopped_up(self):
        o = SmartOptimizer(max_connections=8)
        # 1 MB cannot be split into pieces of at least 1 MB each.
        self.assertEqual(o.segment_count(1 * _MB, True, min_part=1 * _MB), 1)
        # 3 MB is smaller than one target range, so it stays one request.  It
        # used to become three 1 MB ranges purely because the ceiling was
        # applied as a floor, which tripled the requests for no gain.
        self.assertEqual(o.segment_count(3 * _MB, True, min_part=1 * _MB), 1)
        self.assertEqual(o.segment_count(4 * _MB, True, min_part=1 * _MB), 1)

    def test_small_file_keeps_its_natural_range_count(self):
        """Regression: an 8 MB file must not become eight 1 MB requests.

        The whole-wave rounding is only meaningful once a file needs more than
        one wave.  Applying it below that turned 2 natural ranges into 8 and
        doubled the wall-clock time of an 8 MB download (measured 1.03 s
        against 0.52 s for the same file with 2 ranges).
        """
        o = SmartOptimizer(max_connections=8)
        self.assertEqual(o.segment_count(8 * _MB, True, min_part=1 * _MB), 2)
        self.assertEqual(o.segment_count(12 * _MB, True, min_part=1 * _MB), 3)

    def test_medium_file_gets_headroom_beyond_the_connection_ceiling(self):
        o = SmartOptimizer(max_connections=8)
        # 48 MB / 4 MB target = 12 ranges, rounded up to two whole waves of 8.
        self.assertEqual(o.segment_count(48 * _MB, True), 16)

    def test_large_file_is_capped_at_two_waves(self):
        o = SmartOptimizer(max_connections=8)
        self.assertEqual(o.segment_count(4096 * _MB, True), 16)

    def test_plan_is_a_whole_number_of_waves(self):
        """A remainder wave would leave connections idle at the end.

        Only applies once the file needs more than one wave; a 20 MB file gets
        5 natural ranges rather than being padded to 8.
        """
        self.assertEqual(SmartOptimizer(max_connections=8).segment_count(20 * _MB, True), 5)
        for size_mb in (33, 48, 96, 200):
            seg = SmartOptimizer(max_connections=8).segment_count(size_mb * _MB, True)
            self.assertEqual(seg % 8, 0, f"{size_mb} MB produced {seg} ranges")

    def test_plan_respects_a_low_connection_ceiling(self):
        o = SmartOptimizer(max_connections=4)
        self.assertEqual(o.segment_count(4096 * _MB, True), 8)

    def test_no_range_stays_single(self):
        o = SmartOptimizer(max_connections=8)
        self.assertEqual(o.segment_count(2 * 1024 * _MB, False), 1)


class GovernorTest(unittest.TestCase):
    def test_limits_concurrency(self):
        g = ConnectionGovernor(2)
        g.acquire()
        g.acquire()
        entered = threading.Event()

        def worker():
            g.acquire()
            entered.set()
            g.release()

        t = threading.Thread(target=worker)
        t.start()
        self.assertFalse(entered.wait(0.3), "third slot should be blocked")
        g.release()
        self.assertTrue(entered.wait(2.0), "worker should proceed after release")
        t.join(2)

    def test_never_drops_below_one(self):
        g = ConnectionGovernor(1)
        g.set_max_active(0)
        self.assertEqual(g.max_active, 1)


class RampPolicyTest(unittest.TestCase):
    def make(self, ceiling: int = 8) -> tuple:
        clock = FakeClock()
        opt = SmartOptimizer(max_connections=ceiling, adaptive=True,
                             clock=clock, log=lambda *a, **k: None)
        opt.start(1000 * _MB, True, min_part=2 * _MB)
        return opt, clock

    def test_starts_at_the_ceiling(self):
        """A per-connection cap is the normal case, so open the ceiling at once.

        Ramping toward it costs the ramp itself: on a 4 MB/s-per-connection
        link, starting at 4 finished a 48 MB file in 3.5 s and starting at 8 in
        2.0 s.
        """
        opt, _clock = self.make()
        self.assertEqual(opt._governor.max_active, 8)

    def test_small_file_uses_only_its_own_ranges(self):
        clock = FakeClock()
        opt = SmartOptimizer(max_connections=8, adaptive=True, clock=clock)
        seg, gov = opt.start(3 * _MB, True, min_part=1 * _MB)
        # Below one target range there is nothing to parallelise: one range, and
        # therefore no governor to throttle a single connection.
        self.assertEqual(seg, 1)
        self.assertIsNone(gov)

    def test_file_between_target_and_one_wave_uses_its_own_ranges(self):
        clock = FakeClock()
        opt = SmartOptimizer(max_connections=8, adaptive=True, clock=clock)
        seg, gov = opt.start(8 * _MB, True, min_part=1 * _MB)
        self.assertEqual(seg, 2)
        self.assertEqual(gov.max_active, 2)

    def test_non_adaptive_uses_every_range(self):
        clock = FakeClock()
        opt = SmartOptimizer(max_connections=8, adaptive=False, clock=clock)
        seg, gov = opt.start(1000 * _MB, True, min_part=2 * _MB)
        self.assertIsNone(gov)
        self.assertGreater(seg, 1)

    def test_ramps_while_extra_connections_keep_paying(self):
        """A link that scales linearly recovers the ceiling after a backoff."""
        opt, clock = self.make()
        opt.on_server_error(429)                 # knocked down to 4
        self.assertEqual(opt._governor.max_active, 4)
        clock.advance(20)                        # past the error cooldown
        final = drive(opt, clock, bps_per_conn=2 * _MB, conns=8, seconds=30)
        self.assertEqual(final, 8)

    def test_holds_when_extra_connections_buy_nothing(self):
        """Flat throughput no matter the connection count: do not keep probing."""
        opt, clock = self.make()
        opt.on_server_error(429)                 # start below the ceiling
        clock.advance(20)
        final = drive(opt, clock, bps_per_conn=8 * _MB, conns=1, seconds=30)
        self.assertLessEqual(final, 5)

    def test_never_exceeds_the_ceiling(self):
        clock = FakeClock()
        opt = SmartOptimizer(max_connections=6, adaptive=True, clock=clock,
                             log=lambda *a, **k: None)
        opt.start(1000 * _MB, True, min_part=2 * _MB)
        final = drive(opt, clock, bps_per_conn=1 * _MB, conns=6, seconds=60)
        self.assertEqual(final, 6)

    def test_does_not_grow_during_a_speed_limit(self):
        opt, clock = self.make()
        opt.on_server_error(429)
        clock.advance(20)
        opt.set_speed_limited(True)
        self.assertEqual(drive(opt, clock, 2 * _MB, 8, 20), 4)

    def test_server_error_halves_and_cools_down(self):
        opt, clock = self.make()
        opt.on_server_error(429)
        self.assertEqual(opt._governor.max_active, 4)
        # During the cooldown, a strongly improving link must not be chased.
        drive(opt, clock, bps_per_conn=4 * _MB, conns=8, seconds=5)
        self.assertEqual(opt._governor.max_active, 4)

    def test_does_not_change_near_completion(self):
        opt, clock = self.make()
        clock.advance(5)
        before = opt._governor.max_active
        # 99.9 % done: nothing left to gain.
        opt.observe(999 * _MB, 1000 * _MB)
        self.assertEqual(opt._governor.max_active, before)

    def test_backs_off_when_throughput_collapses(self):
        opt, clock = self.make()
        drive(opt, clock, bps_per_conn=2 * _MB, conns=8, seconds=12)
        before = opt._governor.max_active
        self.assertGreater(before, 4)
        # The server starts throttling hard.
        completed = 400 * _MB
        for _ in range(10):
            clock.advance(1.0)
            completed += int(0.2 * _MB)      # 0.2 MB/s total
            opt.observe(completed, 1000 * _MB)
        self.assertLess(opt._governor.max_active, before)

    def test_log_messages_are_formatted_for_console_loggers(self):
        """Console loggers such as Rich print do not apply printf formatting."""
        messages = []
        clock = FakeClock()
        opt = SmartOptimizer(max_connections=8, adaptive=True, clock=clock,
                             log=messages.append)
        opt.start(1000 * _MB, True, min_part=2 * _MB)
        drive(opt, clock, 2 * _MB, 8, 12)
        completed = 400 * _MB
        for _ in range(10):
            clock.advance(1.0)
            completed += int(0.2 * _MB)
            opt.observe(completed, 1000 * _MB)
        collapse = [m for m in messages if "throughput collapsed" in m]
        self.assertTrue(collapse, messages)
        self.assertFalse(
            any(token in m for m in collapse for token in ("%.", "%d", "%s")),
            collapse,
        )
        self.assertTrue(all(" -> " in m for m in collapse), collapse)

    def test_status_callback_reports_changes(self):
        seen = []
        clock = FakeClock()
        opt = SmartOptimizer(max_connections=8, adaptive=True, clock=clock,
                             on_status=seen.append, log=lambda *a, **k: None)
        opt.start(1000 * _MB, True)
        opt.on_server_error(503)                 # 8 -> 4
        clock.advance(20)
        drive(opt, clock, 2 * _MB, 8, 30)        # climbs back
        self.assertTrue(any("->" in s for s in seen), seen)


class ModePolicyTest(unittest.TestCase):
    """The download modes must change policy, not just a connection count.

    A mode that only swapped one integer would be a preset.  These tests assert
    the properties that make each mode mean something: how the ceiling is
    derived, where the controller starts, and how patient it is.
    """

    def test_every_mode_is_retrievable_by_its_own_name(self):
        for name, policy in MODES.items():
            self.assertEqual(policy_for(name).name, name)

    def test_legacy_and_alias_values_map_to_a_mode(self):
        self.assertEqual(policy_for("smart").name, "auto")
        self.assertEqual(policy_for("high").name, "high_performance")
        self.assertEqual(policy_for("performance").name, "high_performance")
        self.assertEqual(policy_for("safe").name, "conservative")
        self.assertEqual(policy_for("AUTO").name, "auto")
        self.assertEqual(policy_for("nonsense").name, "auto")

    def test_manual_is_not_adaptive(self):
        self.assertFalse(is_adaptive("manual"))
        self.assertFalse(is_adaptive("MANUAL"))
        for name in MODES:
            self.assertTrue(is_adaptive(name))
        self.assertTrue(is_adaptive("smart"))

    def test_ceiling_is_scaled_never_replaced_by_a_huge_number(self):
        # The user's explicit maximum is respected as an upper bound.
        self.assertEqual(AUTO_POLICY.ceiling_for(8), 8)
        self.assertEqual(BALANCED_POLICY.ceiling_for(8), 6)
        self.assertEqual(CONSERVATIVE_POLICY.ceiling_for(8), 3)
        self.assertEqual(HIGH_PERFORMANCE_POLICY.ceiling_for(8), 8)

    def test_ceiling_never_reaches_zero(self):
        for policy in MODES.values():
            self.assertGreaterEqual(policy.ceiling_for(1), 1)

    def test_ceiling_is_capped(self):
        self.assertLessEqual(HIGH_PERFORMANCE_POLICY.ceiling_for(64), 16)
        self.assertLessEqual(CONSERVATIVE_POLICY.ceiling_for(64), 4)

    def test_start_never_exceeds_the_ceiling(self):
        for policy in MODES.values():
            for configured in (1, 2, 4, 8, 32):
                ceiling = policy.ceiling_for(configured)
                self.assertLessEqual(policy.start_for(ceiling), ceiling)
                self.assertGreaterEqual(policy.start_for(ceiling), 1)

    def test_conservative_is_stricter_than_high_performance(self):
        self.assertLess(
            CONSERVATIVE_POLICY.ceiling_for(8), HIGH_PERFORMANCE_POLICY.ceiling_for(8)
        )
        self.assertGreater(
            CONSERVATIVE_POLICY.target_segment, HIGH_PERFORMANCE_POLICY.target_segment
        )
        self.assertGreater(
            CONSERVATIVE_POLICY.gain_threshold, HIGH_PERFORMANCE_POLICY.gain_threshold
        )
        self.assertGreater(
            CONSERVATIVE_POLICY.saturation_cooldown,
            HIGH_PERFORMANCE_POLICY.saturation_cooldown,
        )

    def test_auto_is_the_shipped_tuning(self):
        self.assertEqual(AUTO_POLICY.target_segment, TARGET_SEGMENT)
        self.assertEqual(AUTO_POLICY.gain_threshold, GAIN_THRESHOLD)
        self.assertEqual(AUTO_POLICY.ceiling_for(8), 8)

    def test_optimizer_honours_the_policy(self):
        opt = SmartOptimizer(
            max_connections=8, policy=CONSERVATIVE_POLICY, clock=FakeClock()
        )
        self.assertEqual(opt.ceiling, 3)
        seg, gov = opt.start(512 * _MB, True, _MB)
        # Conservative starts low on purpose and has no extra headroom wave.
        self.assertEqual(gov.max_active, CONSERVATIVE_POLICY.start_for(3))
        self.assertLessEqual(gov.max_active, opt.ceiling)

    def test_optimizer_policy_changes_segment_plan(self):
        size = 512 * _MB
        auto = SmartOptimizer(max_connections=8, policy=AUTO_POLICY)
        high = SmartOptimizer(max_connections=8, policy=HIGH_PERFORMANCE_POLICY)
        self.assertGreater(
            high.segment_count(size, True, _MB),
            auto.segment_count(size, True, _MB),
            "high performance should plan more, smaller ranges",
        )


class OptimizerIntegrationTest(unittest.TestCase):
    """Byte-exactness must survive every scheduling decision."""

    def _download(self, handler, adaptive=True):
        with TestServer(handler) as srv:
            cfg = _smart_cfg(max_connections=8, adaptive=adaptive)
            cfg.chunk_size = 256 * 1024
            out = Path(tempfile.mkdtemp())
            ctrl = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            ok = ctrl.download_file(srv.url, out, control=TaskControl())
            self.assertTrue(ok)
            f = out / "file.bin"
            self.assertTrue(f.exists())
            self.assertEqual(f.stat().st_size, len(DATA))
            self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(),
                             hashlib.sha256(DATA).hexdigest())
            return out

    def test_smart_range_server(self):
        self._download(RangeHandler)

    def test_smart_fake_range_server(self):
        # Advertises Range but returns 200 full body — must still be byte-exact.
        self._download(NoRangeHandler)

    def test_smart_no_range_single_stream(self):
        self._download(TrueNoRangeHandler)

    def test_smart_adaptive_and_non_adaptive(self):
        self._download(RangeHandler, adaptive=True)
        self._download(RangeHandler, adaptive=False)

    def test_manual_mode_unchanged(self):
        cfg = AppConfig()
        cfg.block_private_urls = False
        cfg.connection_mode = "manual"
        cfg.num_threads = 4
        cfg.verify_size = True
        with TestServer(RangeHandler) as srv:
            out = Path(tempfile.mkdtemp())
            ctrl = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            self.assertTrue(ctrl.download_file(srv.url, out, control=TaskControl()))
            f = out / "file.bin"
            self.assertEqual(f.stat().st_size, len(DATA))


if __name__ == "__main__":
    unittest.main()


class RetryPressureTest(unittest.TestCase):
    """A stable-but-slow link never collapses, so retries must drive the plan."""

    def make(self):
        from core.optimizer import RETRY_STORM, SmartOptimizer
        clock = FakeClock()
        opt = SmartOptimizer(max_connections=8, clock=clock)
        opt.start(64 * 1024 * 1024, True, 2 * 1024 * 1024)
        return opt, clock, RETRY_STORM

    def test_a_retry_storm_halves_the_connection_count(self):
        opt, clock, storm = self.make()
        before = opt._governor.max_active
        for _ in range(storm):
            clock.advance(0.5)
            opt.note_retry()
        self.assertLess(opt._governor.max_active, before)

    def test_isolated_retries_do_not_back_off(self):
        opt, clock, storm = self.make()
        before = opt._governor.max_active
        for _ in range(storm - 1):
            clock.advance(0.5)
            opt.note_retry()
        self.assertEqual(opt._governor.max_active, before)

    def test_retries_outside_the_window_are_forgotten(self):
        from core.optimizer import RETRY_WINDOW
        opt, clock, storm = self.make()
        before = opt._governor.max_active
        for _ in range(storm):
            clock.advance(RETRY_WINDOW * 0.6)
            opt.note_retry()
        self.assertEqual(opt._governor.max_active, before)

    def test_backoff_is_rate_limited(self):
        opt, clock, storm = self.make()
        for _ in range(storm):
            clock.advance(0.1)
            opt.note_retry()
        once = opt._governor.max_active
        for _ in range(storm * 2):          # still inside the cooldown
            clock.advance(0.1)
            opt.note_retry()
        self.assertEqual(opt._governor.max_active, once)

    def test_backoff_never_goes_below_one(self):
        opt, clock, storm = self.make()
        for _ in range(40):
            clock.advance(30.0)             # past every cooldown
            for _ in range(storm):
                opt.note_retry()
        self.assertGreaterEqual(opt._governor.max_active, 1)


class FairCeilingTest(unittest.TestCase):
    def test_fair_ceiling_caps_the_plan(self):
        from core.optimizer import SmartOptimizer
        opt = SmartOptimizer(max_connections=8, clock=FakeClock())
        wide = opt.segment_count(64 * 1024 * 1024, True, 2 * 1024 * 1024)
        opt.set_fair_ceiling(2)
        narrow = opt.segment_count(64 * 1024 * 1024, True, 2 * 1024 * 1024)
        self.assertLess(narrow, wide)
        self.assertLessEqual(narrow, 2 * opt.policy.segments_per_connection)

    def test_fair_ceiling_caps_starting_connections(self):
        from core.optimizer import SmartOptimizer
        opt = SmartOptimizer(max_connections=8, clock=FakeClock())
        opt.set_fair_ceiling(2)
        # ``start`` returns the number of *ranges*, not connections: the cap is
        # enforced by the governor, so a narrow share keeps the ranges but runs
        # only two of them at a time.
        segments, governor = opt.start(64 * 1024 * 1024, True, 2 * 1024 * 1024)
        self.assertLessEqual(governor.max_active, 2)
        self.assertGreaterEqual(segments, governor.max_active)

    def test_zero_means_uncapped(self):
        from core.optimizer import SmartOptimizer
        opt = SmartOptimizer(max_connections=8, clock=FakeClock())
        opt.set_fair_ceiling(2)
        opt.set_fair_ceiling(0)
        self.assertEqual(opt._effective_ceiling(), opt.ceiling)
