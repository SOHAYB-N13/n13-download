"""Process-wide connection budget.

The budget is the only thing that bounds the *sum* of simultaneous sockets:
per-download concurrency is governed per download and the queue bounds how many
downloads run, but their product was unbounded.  At the settings' maximum
(10 downloads x 8 connections) that is 80 sockets with a 4 MB ``SO_RCVBUF``
each -- roughly 320 MB of kernel buffers on a machine that may have 4 GB.

These tests pin the two properties the rest of the engine relies on:

* the cap actually holds under contention, and
* the cap can never be set below one download's own ceiling, so a lone
  download is never starved by a limit meant for the whole machine.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.budget import (
    DEFAULT_TOTAL,
    ConnectionBudget,
    ConnectionMeter,
    get_connection_budget,
    sync_connection_budget,
)
from tests.helpers import test_config as _test_config


class ConnectionBudgetTest(unittest.TestCase):
    def test_capacity_is_respected(self):
        budget = ConnectionBudget(3)
        for _ in range(3):
            self.assertTrue(budget.acquire())

        acquired = threading.Event()

        def try_fourth():
            budget.acquire()
            acquired.set()

        t = threading.Thread(target=try_fourth, daemon=True)
        t.start()
        self.assertFalse(acquired.wait(0.15), "budget handed out a 4th slot")

        budget.release()
        self.assertTrue(acquired.wait(2.0), "released slot was not reused")
        # Drain so the daemon thread's slot is returned before teardown.
        budget.release()
        budget.release()
        budget.release()
        t.join(timeout=2.0)

    def test_over_release_does_not_raise(self):
        budget = ConnectionBudget(2)
        budget.acquire()
        budget.release()
        # Releasing more than was taken is a bug, but it must never take a
        # download down with it.
        budget.release()
        budget.release()
        self.assertTrue(budget.acquire())

    def test_total_is_at_least_one(self):
        self.assertEqual(ConnectionBudget(0).total, 1)
        self.assertEqual(ConnectionBudget(-5).total, 1)

    def test_peak_holders_never_exceed_total(self):
        budget = ConnectionBudget(4)
        peak = [0]
        current = [0]
        lock = threading.Lock()
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(20):
                    budget.acquire()
                    with lock:
                        current[0] += 1
                        peak[0] = max(peak[0], current[0])
                    time.sleep(0.001)
                    with lock:
                        current[0] -= 1
                    budget.release()
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(errors, [])
        self.assertLessEqual(peak[0], 4, f"peak holders {peak[0]} exceeded cap 4")


class SyncConnectionBudgetTest(unittest.TestCase):
    def setUp(self):
        # Each test starts from a clean global so ordering cannot leak state.
        sync_connection_budget(_test_config(max_total_connections=DEFAULT_TOTAL))

    def test_reads_configured_total(self):
        cfg = _test_config(max_total_connections=12, smart_max_connections=4)
        self.assertEqual(sync_connection_budget(cfg).total, 12)

    def test_never_below_one_downloads_ceiling(self):
        """A global cap smaller than a single download's ceiling would starve
        that download even when it is the only one running."""
        cfg = _test_config(max_total_connections=2, smart_max_connections=8)
        self.assertEqual(sync_connection_budget(cfg).total, 8)

    def test_clamped_to_sane_maximum(self):
        cfg = _test_config(max_total_connections=100000, smart_max_connections=8)
        self.assertEqual(sync_connection_budget(cfg).total, 256)

    def test_invalid_value_falls_back_to_default(self):
        cfg = _test_config()
        cfg.max_total_connections = "not-a-number"
        self.assertEqual(sync_connection_budget(cfg).total, DEFAULT_TOTAL)

    def test_none_falls_back_to_default(self):
        cfg = _test_config()
        cfg.max_total_connections = None
        self.assertEqual(sync_connection_budget(cfg).total, DEFAULT_TOTAL)

    def test_same_total_returns_same_object(self):
        cfg = _test_config(max_total_connections=16, smart_max_connections=8)
        first = sync_connection_budget(cfg)
        self.assertIs(sync_connection_budget(cfg), first)

    def test_changed_total_rebuilds(self):
        cfg = _test_config(max_total_connections=16, smart_max_connections=8)
        first = sync_connection_budget(cfg)
        cfg.max_total_connections = 24
        second = sync_connection_budget(cfg)
        self.assertIsNot(second, first)
        self.assertEqual(second.total, 24)

    def test_get_returns_a_budget_without_config(self):
        budget = get_connection_budget()
        self.assertIsNotNone(budget)
        self.assertGreaterEqual(budget.total, 1)


class ConfigDefaultsTest(unittest.TestCase):
    def test_defaults_are_bounded(self):
        cfg = AppConfig()
        # A default that allows more sockets than the machine can buffer would
        # make the cap decorative.
        self.assertLessEqual(cfg.max_total_connections, 64)
        self.assertGreaterEqual(cfg.max_total_connections, 8)
        # The product of the two concurrency defaults must fit inside it.
        self.assertLessEqual(
            cfg.max_concurrent * cfg.smart_max_connections,
            max(cfg.max_total_connections, cfg.smart_max_connections),
        )

    def test_validation_clamps(self):
        cfg = AppConfig.from_dict({"max_total_connections": 99999})
        self.assertEqual(cfg.max_total_connections, 256)

        cfg = AppConfig.from_dict({"max_total_connections": 0})
        self.assertGreaterEqual(cfg.max_total_connections, 1)


if __name__ == "__main__":
    unittest.main()


class FairShareTest(unittest.TestCase):
    def test_a_single_transfer_is_uncapped(self):
        from core.budget import ConnectionBudget
        budget = ConnectionBudget(32)
        self.assertEqual(budget.enter_task(), 1)
        self.assertEqual(budget.fair_share(), 0)

    def test_share_is_total_divided_by_transfers(self):
        from core.budget import ConnectionBudget
        budget = ConnectionBudget(32)
        for _ in range(4):
            budget.enter_task()
        self.assertEqual(budget.fair_share(), 8)

    def test_share_never_reaches_zero(self):
        from core.budget import ConnectionBudget
        budget = ConnectionBudget(4)
        for _ in range(10):
            budget.enter_task()
        self.assertGreaterEqual(budget.fair_share(), 1)

    def test_leaving_restores_the_share(self):
        from core.budget import ConnectionBudget
        budget = ConnectionBudget(32)
        for _ in range(4):
            budget.enter_task()
        budget.exit_task()
        budget.exit_task()
        self.assertEqual(budget.fair_share(), 16)
        budget.exit_task()
        self.assertEqual(budget.fair_share(), 0)

    def test_exit_below_zero_is_ignored(self):
        from core.budget import ConnectionBudget
        budget = ConnectionBudget(32)
        budget.exit_task()
        self.assertEqual(budget.fair_share(), 0)


class ConnectionMeterTest(unittest.TestCase):
    def test_peak_counts_real_simultaneous_requests(self):
        meter = ConnectionMeter()
        meter.enter()
        meter.enter()
        self.assertEqual(meter.peak, 2)
        meter.exit()
        meter.enter()
        self.assertEqual(meter.peak, 2, "peak must not decay")

    def test_over_exit_cannot_make_peak_negative(self):
        meter = ConnectionMeter()
        meter.exit()
        meter.enter()
        self.assertEqual(meter.peak, 1)

    def test_budget_exposes_process_peak(self):
        budget = ConnectionBudget(4)
        for _ in range(3):
            budget.acquire()
        self.assertEqual(budget.peak_active, 3)
        for _ in range(3):
            budget.release()
