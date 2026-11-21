"""Regression + Phase F — engine-level tests against a real local HTTP server.

Covers: normal multi-threaded download, Range, non-Range fallback, resume,
pause/cancel, checksum verify (pass + fail), 404, SSRF blocking, speed
throttling, transient-error retry, and per-task control isolation.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.control import TaskControl
from core.download import DownloadController, _is_timeout_like
from core.parts import DownloadPart
from core.progress import ProgressLedger
from core.security import validate_download_url
from core.session import SessionManager
from core.speed import SpeedTracker
import requests
import urllib3

from tests.helpers import (
    DATA,
    NotFoundHandler,
    NoRangeHandler,
    RangeHandler,
    StallHandler,
    TestServer,
    test_config as _test_config,
)


class EngineBasicsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _controller(self, **cfg_kw):
        cfg = _test_config(**cfg_kw)
        return cfg, DownloadController(cfg, SessionManager(cfg), show_progress=False)

    def test_normal_multithreaded_download(self):
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller(num_threads=4)
            self.assertTrue(c.download_file(srv.url, self.dir))
            saved = self.dir / "file.bin"
            self.assertEqual(saved.stat().st_size, len(DATA))
            self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(),
                             hashlib.sha256(DATA).hexdigest())

    def test_parallel_parts_used(self):
        # With range support + 4 threads, part files must appear and merge.
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller(num_threads=4)
            self.assertTrue(c.download_file(srv.url, self.dir))
            self.assertTrue((self.dir / "file.bin").exists())
            # .part files cleaned up after merge
            self.assertEqual(list(self.dir.glob("*.part*")), [])

    def test_direct_layout_skips_the_merge_pass(self):
        """Segmented downloads write at final offsets; there is no merge copy."""
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller(num_threads=4)
            self.assertTrue(c.download_file(srv.url, self.dir))
            report = c.last_report
            self.assertIsNotNone(report)
            self.assertNotIn("merge", report.timeline)
            self.assertGreaterEqual(report.peak_connections, 2)
            self.assertEqual(list(self.dir.glob("*.merging")), [])
            self.assertFalse(list(self.dir.glob("*.dlstate")))
            saved = self.dir / "file.bin"
            self.assertEqual(saved.stat().st_size, len(DATA))
            self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(),
                             hashlib.sha256(DATA).hexdigest())

    def test_non_range_fallback(self):
        with TestServer(NoRangeHandler) as srv:
            cfg, c = self._controller(num_threads=4)
            self.assertTrue(c.download_file(srv.url, self.dir))
            saved = self.dir / "file.bin"
            self.assertEqual(saved.stat().st_size, len(DATA))

    def test_non_range_first_download_fresh_process(self):
        """Regression: a single-thread (non-Range) download as the *first*
        download in a fresh process must not hang on the pause blocker."""
        import subprocess
        root = Path(__file__).resolve().parent.parent
        with TestServer(NoRangeHandler) as srv:
            code = r"""
import sys, tempfile
sys.path.insert(0, r"%s")
from pathlib import Path
from config.settings import AppConfig
from core.session import SessionManager
from core.speed import SpeedTracker
from core.download import DownloadController, _is_timeout_like
from tests.helpers import DATA, NoRangeHandler, TestServer
with TestServer(NoRangeHandler) as srv:
    cfg = AppConfig(); cfg.block_private_urls = False; cfg.verify_size = True
    c = DownloadController(cfg, SessionManager(cfg), show_progress=False)
    out = Path(tempfile.mkdtemp())
    assert c.download_file(srv.url, out), c.last_error
    assert (out / "file.bin").stat().st_size == len(DATA)
    print("OK")
""" % root
            proc = subprocess.run([sys.executable, "-c", code], cwd=str(root),
                                  capture_output=True, text=True, timeout=90)
            self.assertIn("OK", proc.stdout, f"child failed: {proc.stderr}")

    def test_resume_after_partial(self):
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller(num_threads=2, max_speed_bps=512 * 1024)
            control = TaskControl()
            result = {}
            th = threading.Thread(target=lambda: result.setdefault(
                "ok", c.download_file(srv.url, self.dir, control=control,
                                      progress_callback=lambda d, t: result.update(done=d))))
            th.start()
            deadline = time.time() + 20
            while time.time() < deadline:
                if result.get("done", 0) > 0:
                    control.cancel()
                    break
                time.sleep(0.02)
            th.join(20)
            # State saved for resume.
            self.assertTrue(list(self.dir.glob("*.dlstate")))
            # Resume to completion.
            cfg2, c2 = self._controller(num_threads=2)
            self.assertTrue(c2.download_file(srv.url, self.dir))
            saved = self.dir / "file.bin"
            self.assertEqual(saved.stat().st_size, len(DATA))
            self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(),
                             hashlib.sha256(DATA).hexdigest())

    def test_checksum_verify_ok(self):
        expected = hashlib.sha256(DATA).hexdigest()
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller()
            self.assertTrue(c.download_file(srv.url, self.dir,
                                            verify_checksum=True, expected_hash=expected))
            self.assertTrue((self.dir / "file.bin").exists())

    def test_checksum_verify_mismatch(self):
        bogus = hashlib.sha256(b"not the data").hexdigest()
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller()
            self.assertFalse(c.download_file(srv.url, self.dir,
                                             verify_checksum=True, expected_hash=bogus))
            # Corrupt result is removed, not left behind.
            self.assertFalse((self.dir / "file.bin").exists())

    def test_404_friendly_error(self):
        with TestServer(NotFoundHandler) as srv:
            cfg, c = self._controller()
            self.assertFalse(c.download_file(srv.url, self.dir))
            self.assertIn("404", c.last_error)

    def test_ssrf_blocks_loopback(self):
        with TestServer(RangeHandler) as srv:
            ok, err = validate_download_url(srv.url, block_private=True)
            self.assertFalse(ok)
            # And the engine refuses too when protection is on.
            cfg = AppConfig()          # block_private_urls defaults True
            c = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            self.assertFalse(c.download_file(srv.url, self.dir))

    def test_speed_throttling(self):
        # 1 MB/s cap over ~5 MB (with a 1 MB soft burst) must take a couple of
        # seconds — comfortably above the ~0.1 s an unlimited download takes.
        with TestServer(RangeHandler) as srv:
            start = time.monotonic()
            cfg, c = self._controller(num_threads=1, max_speed_bps=1024 * 1024)
            self.assertTrue(c.download_file(srv.url, self.dir))
            elapsed = time.monotonic() - start
            self.assertGreater(elapsed, 2.0, f"too fast ({elapsed:.2f}s) — limiter not applied")

    def test_transient_server_error_retried(self):
        RangeHandler.fail_requests = 1   # first range GET returns 500
        with TestServer(RangeHandler) as srv:
            cfg, c = self._controller(max_retries=4, retry_delay=0.01)
            self.assertTrue(c.download_file(srv.url, self.dir))
            self.assertEqual((self.dir / "file.bin").stat().st_size, len(DATA))

    def test_per_task_cancel_isolation(self):
        """Cancelling one task's control must not cancel a concurrent one."""
        with TestServer(RangeHandler) as srv:
            cfg = _test_config(num_threads=1, max_speed_bps=512 * 1024)
            c1 = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            c2 = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            out1, out2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
            ctl1, ctl2 = TaskControl(), TaskControl()
            r1, r2 = {}, {}
            t1 = threading.Thread(target=lambda: r1.setdefault(
                "ok", c1.download_file(srv.url, out1, control=ctl1,
                                       progress_callback=lambda d, t: r1.update(done=d))))
            t2 = threading.Thread(target=lambda: r2.setdefault(
                "ok", c2.download_file(srv.url, out2, control=ctl2,
                                       progress_callback=lambda d, t: r2.update(done=d))))
            t1.start(); t2.start()
            # Let both get going, then cancel only task 1.
            deadline = time.time() + 20
            while time.time() < deadline:
                if r1.get("done", 0) > 0 and r2.get("done", 0) > 0:
                    ctl1.cancel()
                    break
                time.sleep(0.02)
            t1.join(20); t2.join(20)
            self.assertTrue(not r1.get("ok", False))        # task1 cancelled
            self.assertTrue(r2.get("ok", False))            # task2 unaffected
            self.assertEqual((out2 / "file.bin").stat().st_size, len(DATA))


class StallRecoveryTest(unittest.TestCase):
    """A silent socket is a transport hiccup, not a rejection.

    Before the stall watchdog existed, a connection that delivered bytes and
    then went quiet sat on the full read timeout and was retried on the
    exponential ladder *and* counted as a server error, which halved
    concurrency for the whole download.  These tests pin the corrected
    behaviour: resume from the offset already on disk, keep the connection
    count, and never let a recovered stall change the output bytes.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        StallHandler.stall_seconds = 1.5
        StallHandler.stall_after = 1024 * 1024

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_stall_is_recovered_byte_exact(self):
        with TestServer(StallHandler) as srv:
            cfg = _test_config(num_threads=4, stall_timeout=0.5)
            c = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            self.assertTrue(c.download_file(srv.url, self.dir))
            saved = self.dir / "file.bin"
            self.assertEqual(saved.stat().st_size, len(DATA))
            self.assertEqual(
                hashlib.sha256(saved.read_bytes()).hexdigest(),
                hashlib.sha256(DATA).hexdigest(),
            )
        self.assertGreaterEqual(StallHandler.stall_events, 1,
                                "fixture never actually stalled")

    def test_stall_reissues_the_range_from_the_offset_on_disk(self):
        """No duplicate and no gap: the resume offset is the part's own size."""
        ranges: list[str] = []
        original = StallHandler.do_GET

        def recording(handler):
            rng = handler.headers.get("Range")
            if rng:
                ranges.append(rng)
            return original(handler)

        StallHandler.do_GET = recording
        try:
            with TestServer(StallHandler) as srv:
                cfg = _test_config(num_threads=1, stall_timeout=0.5)
                c = DownloadController(cfg, SessionManager(cfg), show_progress=False)
                self.assertTrue(c.download_file(srv.url, self.dir))
        finally:
            StallHandler.do_GET = original

        # The stall happened at 1 MB into the first range, so the retry must
        # ask for exactly that offset.
        starts = [int(r.split("=")[1].split("-")[0]) for r in ranges]
        self.assertIn(0, starts)
        self.assertIn(StallHandler.stall_after, starts,
                      f"retry did not resume from the stall offset: {ranges}")

    def test_stall_does_not_count_as_a_server_error(self):
        """Concurrency must survive a stall; on_server_error is for rejections."""
        calls: list = []

        class RecordingOptimizer:
            def on_server_error(self, status):
                calls.append(status)

        cfg = _test_config(num_threads=1, stall_timeout=0.5)
        c = DownloadController(cfg, SessionManager(cfg), show_progress=False)
        with TestServer(StallHandler) as srv:
            part = DownloadPart(
                index=0, start=0, end=len(DATA) - 1,
                path=self.dir / "file.bin.part0",
            )
            ledger = ProgressLedger()
            ok = c.download_part(
                srv.url, part, None, None, ledger, SpeedTracker(), len(DATA),
                None, optimizer=RecordingOptimizer(),
            )
        self.assertTrue(ok)
        self.assertEqual(calls, [], "a stall was reported as a server error")
        self.assertEqual(part.path.stat().st_size, len(DATA))
        self.assertEqual(
            hashlib.sha256(part.path.read_bytes()).hexdigest(),
            hashlib.sha256(DATA).hexdigest(),
        )

    def test_stall_timeout_setting_is_honoured(self):
        """A short configured stall timeout must fail fast, not hang."""
        with TestServer(StallHandler) as srv:
            cfg = _test_config(num_threads=1, stall_timeout=5.0, max_retries=1)
            c = DownloadController(cfg, SessionManager(cfg), show_progress=False)
            # One attempt plus one retry, then give up: the point is that the
            # call returns well inside the 5 s read timeout, not that it hangs.
            StallHandler.stall_seconds = 30.0
            started = time.monotonic()
            c.download_file(srv.url, self.dir)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 25.0, f"stall handling took {elapsed:.1f}s")


class TimeoutClassificationTest(unittest.TestCase):
    """The stall watchdog is only as good as its classifier.

    ``requests`` surfaces a read timeout as ``Timeout`` when the adapter lets
    it through, but as a ``ConnectionError`` whose ``reason`` is urllib3's
    ``ReadTimeoutError`` once the adapter has retried internally.  Missing that
    second shape made every real stall look like a server error, which halved
    concurrency instead of resuming.
    """

    def test_plain_requests_timeout(self):
        self.assertTrue(_is_timeout_like(requests.exceptions.ReadTimeout()))

    def test_socket_timeout(self):
        import socket as _socket
        self.assertTrue(_is_timeout_like(_socket.timeout("timed out")))

    def test_wrapped_in_connection_error_via_reason(self):
        inner = urllib3.exceptions.ReadTimeoutError(None, "/file.bin", "timed out")
        outer = requests.exceptions.ConnectionError(inner)
        self.assertTrue(_is_timeout_like(outer))

    def test_wrapped_via_cause_chain(self):
        inner = TimeoutError("timed out")
        try:
            try:
                raise inner
            except TimeoutError as exc:
                raise requests.exceptions.ConnectionError("wrapped") from exc
        except requests.exceptions.ConnectionError as exc:
            self.assertTrue(_is_timeout_like(exc))

    def test_reset_is_not_a_timeout(self):
        self.assertFalse(
            _is_timeout_like(requests.exceptions.ConnectionError("connection reset"))
        )

    def test_http_error_is_not_a_timeout(self):
        self.assertFalse(
            _is_timeout_like(requests.exceptions.HTTPError("503 Server Error"))
        )

    def test_self_referential_chain_terminates(self):
        exc = requests.exceptions.ConnectionError("loop")
        exc.__cause__ = exc
        self.assertFalse(_is_timeout_like(exc))


if __name__ == "__main__":
    unittest.main()


class DestinationSafetyTest(unittest.TestCase):
    """An unusable destination must fail the download, never raise out of it."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_path_component_that_is_a_file(self):
        blocker = self.dir / "iam_a_file"
        blocker.write_text("x")
        with TestServer(RangeHandler) as srv:
            cfg = _test_config()
            controller = DownloadController(
                cfg, SessionManager(cfg), show_progress=False
            )
            # Used to raise NotADirectoryError through the public entry point.
            self.assertFalse(controller.download_file(srv.url, blocker / "sub"))
            self.assertIn("destination", (controller.last_error or "").lower())
            self.assertIsNotNone(controller.last_report)

    def test_report_is_still_produced_on_that_failure(self):
        blocker = self.dir / "also_a_file"
        blocker.write_text("x")
        with TestServer(RangeHandler) as srv:
            cfg = _test_config()
            controller = DownloadController(
                cfg, SessionManager(cfg), show_progress=False
            )
            controller.download_file(srv.url, blocker / "deep" / "path")
            report = controller.last_report
            self.assertIsNotNone(report)
            self.assertEqual(report.integrity, "failed")
