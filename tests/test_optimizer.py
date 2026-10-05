"""Smart Download Optimizer — focused unit + integration validation."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.control import TaskControl
from core.download import DownloadController
from core.optimizer import ConnectionGovernor, SmartOptimizer
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


class OptimizerUnitTest(unittest.TestCase):
    def test_initial_segment_count_by_size(self):
        o = SmartOptimizer(max_connections=8)
        self.assertEqual(o.segment_count(0, True), 1)          # empty/unknown
        self.assertEqual(o.segment_count(500 * 1024, True), 1)  # very small
        self.assertEqual(o.segment_count(5 * _MB, True), 2)     # small
        self.assertEqual(o.segment_count(60 * _MB, True), 4)    # medium
        self.assertEqual(o.segment_count(300 * _MB, True), 6)   # large
        self.assertEqual(o.segment_count(2 * 1024 * _MB, True), 8)  # very large

    def test_no_range_means_single_connection(self):
        o = SmartOptimizer(max_connections=8)
        self.assertEqual(o.segment_count(2 * 1024 * _MB, False), 1)
        self.assertEqual(o.segment_count(0, False), 1)

    def test_segment_count_capped_by_max(self):
        o = SmartOptimizer(max_connections=4)
        self.assertEqual(o.segment_count(2 * 1024 * _MB, True), 4)
        self.assertEqual(o.segment_count(300 * _MB, True), 4)

    def test_adaptive_start_governor(self):
        seg, gov = SmartOptimizer(max_connections=8, adaptive=True).start(2 * 1024 * _MB, True)
        self.assertEqual(seg, 8)
        self.assertIsNotNone(gov)
        self.assertEqual(gov.max_active, 4)  # conservative start

    def test_non_adaptive_starts_all(self):
        seg, gov = SmartOptimizer(max_connections=8, adaptive=False).start(2 * 1024 * _MB, True)
        self.assertEqual(seg, 8)
        self.assertIsNone(gov)               # no pacing when adaptivity is off

    def test_governor_limits_concurrency(self):
        g = ConnectionGovernor(2)
        g.acquire(); g.acquire()
        # A third acquire must block until one is released.
        from threading import Event
        entered = Event()
        done = []

        def worker():
            g.acquire()
            entered.set()
            done.append(1)
            g.release()

        import threading
        t = threading.Thread(target=worker)
        t.start()
        self.assertFalse(entered.wait(0.3), "third slot should be blocked")
        g.release()
        self.assertTrue(entered.wait(2.0), "worker should proceed after release")
        t.join(2)

    def test_server_error_backoff_and_cooldown(self):
        o = SmartOptimizer(max_connections=8, adaptive=True,
                           log=lambda *a, **k: None)
        o.start(2 * 1024 * _MB, True)
        self.assertEqual(o._governor.max_active, 4)
        o.on_server_error(429)
        self.assertEqual(o._governor.max_active, 2)  # halved
        # During the error cooldown, no increase happens.
        o._baseline = True
        o._prev_inst = 100.0
        o.observe(5 * _MB, 1000 * _MB)   # would normally increase
        self.assertEqual(o._governor.max_active, 2)

    def test_speed_limit_blocks_increase(self):
        o = SmartOptimizer(max_connections=8, adaptive=True,
                           log=lambda *a, **k: None)
        o.start(2 * 1024 * _MB, True)
        o.set_speed_limited(True)
        o._baseline = True
        o._prev_inst = 10.0
        # High throughput improvement, but a speed limit is active → no change.
        o.observe(5 * _MB, 1000 * _MB)
        self.assertEqual(o._governor.max_active, 4)

    def test_increase_after_stable_improvement(self):
        o = SmartOptimizer(max_connections=8, adaptive=True,
                           log=lambda *a, **k: None)
        o.start(2 * 1024 * _MB, True)
        # Force past warm-up/cooldown timestamps.
        o._start = time.monotonic() - 10
        o._last_change = 0.0
        o.observe(10 * _MB, 1000 * _MB)   # baseline sample
        o._last_obs_time = time.monotonic() - 2  # next observe samples a delta
        # A strongly improved instantaneous rate should trigger +2.
        o._prev_inst = 5.0
        o.observe(20 * _MB, 1000 * _MB)
        self.assertEqual(o._governor.max_active, 6)


class OptimizerIntegrationTest(unittest.TestCase):
    def _download(self, handler, adaptive=True, num_threads=4):
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
        # Server advertises Range but returns 200 full body — Smart must still
        # produce a byte-exact file.
        self._download(NoRangeHandler)

    def test_smart_no_range_single_stream(self):
        self._download(TrueNoRangeHandler)

    def test_smart_adaptive_and_non_adaptive(self):
        self._download(RangeHandler, adaptive=True)
        self._download(RangeHandler, adaptive=False)

    def test_manual_mode_unchanged(self):
        # Manual mode must use the configured num_threads exactly.
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
