"""Phase G — scheduler tests (queue gate + night speed override)."""

from __future__ import annotations

import sys
import unittest
from datetime import time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.scheduler import Scheduler, _in_window, _parse_hhmm


class WindowTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(_parse_hhmm("23:00"), dtime(23, 0))
        self.assertEqual(_parse_hhmm("07:05"), dtime(7, 5))
        self.assertIsNone(_parse_hhmm("nope"))
        self.assertIsNone(_parse_hhmm(None))

    def test_in_window_normal(self):
        self.assertTrue(_in_window(dtime(10, 0), dtime(9, 0), dtime(11, 0)))
        self.assertFalse(_in_window(dtime(8, 0), dtime(9, 0), dtime(11, 0)))
        self.assertFalse(_in_window(dtime(11, 0), dtime(9, 0), dtime(11, 0)))

    def test_in_window_overnight(self):
        # 23:00 -> 07:00 wraps midnight.
        self.assertTrue(_in_window(dtime(23, 30), dtime(23, 0), dtime(7, 0)))
        self.assertTrue(_in_window(dtime(3, 0), dtime(23, 0), dtime(7, 0)))
        self.assertFalse(_in_window(dtime(12, 0), dtime(23, 0), dtime(7, 0)))


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = AppConfig()
        self.gates = []
        self.speeds = []
        self.s = Scheduler(self.cfg, on_gate=self.gates.append, on_speed=self.speeds.append)

    def test_disabled_clears_gate_and_restores_speed(self):
        self.cfg.scheduler_enabled = False
        self.cfg.max_speed_bps = 0
        self.s._tick()
        self.assertEqual(self.gates[-1], False)
        self.assertEqual(self.speeds[-1], 0)

    def test_disabled_preserves_configured_speed(self):
        self.cfg.scheduler_enabled = False
        self.cfg.max_speed_bps = 2 * 1024 * 1024
        self.s._tick()
        self.assertEqual(self.speeds[-1], 2 * 1024 * 1024)

    def test_schedule_window_gates_queue(self):
        self.cfg.scheduler_enabled = True
        self.cfg.schedule_start_time = "23:59"
        self.s._tick()
        self.assertEqual(self.gates[-1], True)

    def test_night_cap_applied(self):
        self.cfg.scheduler_enabled = True
        self.cfg.night_speed_limit_bps = 512 * 1024
        self.cfg.night_start_time = "23:00"
        self.cfg.night_end_time = "07:00"
        # Inject "now" inside the night window by monkey-patching the helper.
        from core import scheduler as mod
        mod._now_time = lambda: dtime(2, 0)
        self.s._tick()
        self.assertEqual(self.speeds[-1], 512 * 1024)
        # Outside the window: configured speed restored.
        mod._now_time = lambda: dtime(12, 0)
        self.s._tick()
        self.assertEqual(self.speeds[-1], 0)

    def test_start_stop_thread(self):
        import time
        self.cfg.scheduler_enabled = True
        self.cfg.schedule_start_time = "23:59"
        self.s.start()
        time.sleep(0.3)
        self.assertTrue(self.gates)   # at least one tick fired
        self.s.stop()


class ThrottleOverrideTest(unittest.TestCase):
    def test_override_takes_precedence(self):
        from core import throttle
        throttle._global_limiter = None
        cfg = AppConfig()
        cfg.max_speed_bps = 2 * 1024 * 1024
        throttle.sync_limiter_from_config(cfg)
        self.assertEqual(throttle.get_global_limiter().max_rate, 2 * 1024 * 1024)
        throttle.set_schedule_override(256 * 1024)
        throttle.sync_limiter_from_config(cfg)
        self.assertEqual(throttle.get_global_limiter().max_rate, 256 * 1024)
        throttle.set_schedule_override(None)
        throttle.sync_limiter_from_config(cfg)
        self.assertEqual(throttle.get_global_limiter().max_rate, 2 * 1024 * 1024)
        throttle._global_limiter = None


if __name__ == "__main__":
    unittest.main()
