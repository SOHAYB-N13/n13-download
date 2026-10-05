"""Regression tests for download STARTUP latency.

Guards against the pre-first-byte stall problems found by the performance
audit:

* the exponential retry-sleep chain (3/6/12/24 s ...) BEFORE the first byte
  used to delay recoverable downloads by 20-160 s;
* the probe (HEAD) used to be a 60-second sequential gate and urllib3's read
  retries multiplied read timeouts;
* a failed probe used to abort the download even when the transfer itself
  would have worked.

These tests assert bounded startup times against real local HTTP servers.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.download import DownloadController, _retry_delay
from core.session import SessionManager

DATA = os.urandom(4 * 1024 * 1024 + 4096)  # ~4 MB


# ---------------------------------------------------------------------------
# Local test servers
# ---------------------------------------------------------------------------

class BaseHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass


class FastHandler(BaseHandler):
    n503 = 0
    drop = 0
    head_delay = 0.0

    def do_HEAD(self):
        if FastHandler.drop > 0:
            FastHandler.drop -= 1
            self.connection.close()
            return
        if FastHandler.head_delay > 0:
            time.sleep(FastHandler.head_delay)
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()

    def do_GET(self):
        if FastHandler.drop > 0:
            FastHandler.drop -= 1
            self.connection.close()
            return
        if FastHandler.n503 > 0:
            FastHandler.n503 -= 1
            self.send_response(503)
            self.end_headers()
            return
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            chunk = DATA[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA)-1}/{len(DATA)}")
        else:
            chunk = DATA
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        try:
            self.wfile.write(chunk)
        except Exception:
            pass


class NoRangeHandler(BaseHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()
        try:
            self.wfile.write(DATA)
        except Exception:
            pass


class Head405Handler(BaseHandler):
    def do_HEAD(self):
        self.send_response(405)
        self.end_headers()

    def do_GET(self):
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            chunk = DATA[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA)-1}/{len(DATA)}")
        else:
            chunk = DATA
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        try:
            self.wfile.write(chunk)
        except Exception:
            pass


class RedirectHandler(BaseHandler):
    def do_HEAD(self):
        self.send_response(302)
        self.send_header("Location", "/file.bin")
        self.end_headers()

    def do_GET(self):
        if self.path != "/file.bin":
            self.send_response(302)
            self.send_header("Location", "/file.bin")
            self.end_headers()
            return
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            chunk = DATA[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA)-1}/{len(DATA)}")
        else:
            chunk = DATA
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        try:
            self.wfile.write(chunk)
        except Exception:
            pass


class BlackholeHandler(BaseHandler):
    def do_HEAD(self):
        time.sleep(3600)

    def do_GET(self):
        time.sleep(3600)


class Server:
    """Context manager around a ThreadingHTTPServer."""

    def __init__(self, handler_cls):
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)

    def __enter__(self):
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}/file.bin"
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        return False


def make_config(**overrides) -> AppConfig:
    cfg = AppConfig()
    cfg.block_private_urls = False
    cfg.verify_size = False
    cfg.connection_mode = "manual"
    cfg.num_threads = 4
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


class StartupLatencyTest(unittest.TestCase):
    """Bounded-startup tests using PRODUCTION retry defaults (retry_delay=3).

    The pre-first-byte retry schedule must keep recoverable downloads inside a
    few seconds even though the configured base delay is 3 s.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        FastHandler.n503 = 0
        FastHandler.drop = 0
        FastHandler.head_delay = 0.0

    def _download(self, url, cfg=None, label="audit") -> tuple[bool, float, str]:
        cfg = cfg or make_config()
        c = DownloadController(cfg, SessionManager(cfg), show_progress=False)
        t0 = time.monotonic()
        ok = c.download_file(url, self.dir / label)
        return ok, time.monotonic() - t0, c.last_error

    # --- mandatory regression: 503,503,503,200 ---------------------------

    def test_healthy_server_starts_fast(self):
        with Server(FastHandler) as srv:
            ok, dt, err = self._download(srv.url)
        self.assertTrue(ok, err)
        self.assertLess(dt, 5.0)

    def test_recovery_after_one_503(self):
        with Server(FastHandler) as srv:
            FastHandler.n503 = 1
            ok, dt, err = self._download(srv.url, label="one")
        self.assertTrue(ok, err)
        self.assertLess(dt, 6.0)

    def test_recovery_after_two_503(self):
        with Server(FastHandler) as srv:
            FastHandler.n503 = 2
            ok, dt, err = self._download(srv.url, label="two")
        self.assertTrue(ok, err)
        self.assertLess(dt, 8.0)

    def test_recovery_after_three_503(self):
        with Server(FastHandler) as srv:
            FastHandler.n503 = 3
            ok, dt, err = self._download(srv.url, label="three")
        self.assertTrue(ok, err)
        # Old behaviour: ~21 s of retry sleeps.  New behaviour: ~3.5 s.
        self.assertLess(dt, 10.0)

    def test_recovery_after_four_503(self):
        with Server(FastHandler) as srv:
            FastHandler.n503 = 4
            ok, dt, err = self._download(srv.url, label="four")
        self.assertTrue(ok, err)
        # Old behaviour: ~45 s.  New behaviour: ~5.5 s.
        self.assertLess(dt, 12.0)

    def test_recovery_after_five_503(self):
        with Server(FastHandler) as srv:
            FastHandler.n503 = 5
            ok, dt, err = self._download(srv.url, label="five")
        self.assertTrue(ok, err)
        self.assertLess(dt, 14.0)

    def test_connection_reset_recovers(self):
        with Server(FastHandler) as srv:
            FastHandler.drop = 2
            ok, dt, err = self._download(srv.url, label="reset")
        self.assertTrue(ok, err)
        self.assertLess(dt, 15.0)

    # --- probe behaviour --------------------------------------------------

    def test_slow_head_does_not_gate_download(self):
        """HEAD delayed beyond the probe read timeout, GET fast -> fast start."""
        cfg = make_config(probe_connect_timeout=2, probe_read_timeout=2)
        with Server(FastHandler) as srv:
            FastHandler.head_delay = 6.0
            ok, dt, err = self._download(srv.url, cfg, label="slowhead")
        self.assertTrue(ok, err)
        self.assertLess(dt, 12.0)

    def test_head_405_falls_back_to_get(self):
        with Server(Head405Handler) as srv:
            ok, dt, err = self._download(srv.url, label="head405")
        self.assertTrue(ok, err)
        self.assertLess(dt, 8.0)

    def test_no_range_single_stream(self):
        with Server(NoRangeHandler) as srv:
            ok, dt, err = self._download(srv.url, label="norange")
        self.assertTrue(ok, err)
        self.assertLess(dt, 8.0)

    def test_range_multi_part(self):
        with Server(FastHandler) as srv:
            ok, dt, err = self._download(srv.url, label="multi")
        self.assertTrue(ok, err)
        self.assertLess(dt, 8.0)

    def test_redirect(self):
        with Server(RedirectHandler) as srv:
            ok, dt, err = self._download(srv.url, label="redirect")
        self.assertTrue(ok, err)
        self.assertLess(dt, 10.0)

    # --- blackhole (accepts TCP, never responds) --------------------------

    def test_blackhole_fails_bounded(self):
        """A non-responsive endpoint must fail within a bounded time."""
        cfg = make_config(
            probe_connect_timeout=2,
            probe_read_timeout=2,
            startup_connect_timeout=2,
            startup_read_timeout=2,
            startup_max_attempts=3,
        )
        with Server(BlackholeHandler) as srv:
            ok, dt, err = self._download(srv.url, cfg, label="blackhole")
        self.assertFalse(ok)
        # Old behaviour: minutes (60 s read timeout x3, twice).  New: bounded.
        self.assertLess(dt, 45.0)

    # --- permanent errors fail fast ---------------------------------------

    def test_permanent_error_fails_fast(self):
        class NotFoundHandler(BaseHandler):
            def do_HEAD(self):
                self.send_response(404)
                self.end_headers()

            def do_GET(self):
                self.send_response(404)
                self.end_headers()

        with Server(NotFoundHandler) as srv:
            ok, dt, err = self._download(srv.url, label="n404")
        self.assertFalse(ok)
        self.assertLess(dt, 10.0)


class RetryDelayUnitTest(unittest.TestCase):
    def test_pre_first_byte_schedule_is_fast_and_bounded(self):
        cfg = AppConfig()
        cfg.retry_delay = 3.0      # production default
        cfg.retry_jitter = 0.0
        for attempt in (1, 2, 3, 4, 5, 6):
            d = _retry_delay(attempt, cfg, started=False)
            self.assertLessEqual(d, 2.0)
        self.assertLessEqual(_retry_delay(1, cfg, started=False), 0.5)
        self.assertLessEqual(_retry_delay(2, cfg, started=False), 1.0)

    def test_after_first_byte_keeps_configured_backoff(self):
        cfg = AppConfig()
        cfg.retry_delay = 3.0
        cfg.retry_backoff = 2.0
        cfg.retry_jitter = 0.0
        self.assertAlmostEqual(_retry_delay(1, cfg, started=True), 3.0)
        self.assertAlmostEqual(_retry_delay(2, cfg, started=True), 6.0)
        self.assertAlmostEqual(_retry_delay(3, cfg, started=True), 12.0)

    def test_pre_first_byte_respects_smaller_configured_delay(self):
        cfg = AppConfig()
        cfg.retry_delay = 0.01
        cfg.retry_jitter = 0.0
        self.assertAlmostEqual(_retry_delay(1, cfg, started=False), 0.01)
        self.assertAlmostEqual(_retry_delay(2, cfg, started=False), 0.02)


if __name__ == "__main__":
    unittest.main()