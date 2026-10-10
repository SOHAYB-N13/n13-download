"""Regression tests for download *startup latency* and *progress honesty*.

Every case here reproduces a reported symptom against a controlled local server
(never a public site) with bounded timeouts:

* a download that took 20-40 s to start, or never started;
* a transfer frozen before the first byte;
* progress that sat still and then jumped;
* a status line that claimed "Downloading" while nothing was arriving.

The assertions are structural where possible (event counts, ordering, byte
values) rather than wall-clock, so a loaded machine cannot make them flaky;
where a duration *is* the point, the budget is patched small and the assertion
carries generous headroom.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.download as dl
import core.probe as probe_mod
from config.settings import AppConfig
from core.analyzer import analyze_url
from core.control import TaskControl
from core.download import DownloadController
from core.session import SessionManager

DATA = os.urandom(3 * 1024 * 1024 + 999)   # ~3 MB


class BehaviourHandler(BaseHTTPRequestHandler):
    """One server, several server-side behaviours selected by ``mode``."""

    protocol_version = "HTTP/1.1"
    mode = "ok"
    head_delay = 30.0      # longer than any probe timeout
    body_piece = 128 * 1024
    body_pause = 0.01
    payload = DATA
    connections = 0

    def log_message(self, *a):
        pass

    def setup(self):
        super().setup()
        BehaviourHandler.connections += 1

    # ------------------------------------------------------------------ HEAD
    def do_HEAD(self):
        if self.mode == "all_silent":
            time.sleep(self.head_delay)      # not even HEAD is answered
            return
        if self.mode == "head_hang":
            time.sleep(self.head_delay)
            return
        if self.mode == "head_405":
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.mode == "no_length":
            # Chunked server: no length anywhere, so the total is genuinely
            # unknown and the engine must not invent one.
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            return
        self._send_headers(200, len(self.payload))

    # ------------------------------------------------------------------- GET
    def do_GET(self):
        if self.mode in ("silent", "all_silent"):
            time.sleep(self.head_delay)      # accept, then say nothing at all
            return
        rng = self.headers.get("Range")
        start = 0
        if rng:
            try:
                start = int(rng.split("=")[1].split("-")[0])
            except (IndexError, ValueError):
                start = 0
        body = self.payload[start:]
        if self.mode == "no_length":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for i in range(0, len(body), self.body_piece):
                    piece = body[i:i + self.body_piece]
                    self.wfile.write(b"%x\r\n" % len(piece) + piece + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        status = 206 if (rng and self.mode != "range_liar") else 200
        self._send_headers(status, len(body), start=start)
        try:
            if self.mode == "trickle":
                for i in range(0, len(body), self.body_piece):
                    self.wfile.write(body[i:i + self.body_piece])
                    self.wfile.flush()
                    time.sleep(self.body_pause)
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_headers(self, status, length, start=0):
        self.send_response(status)
        if status == 206:
            self.send_header(
                "Content-Range",
                f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}",
            )
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.end_headers()


class _Server:
    def __init__(self, mode: str, **attrs):
        BehaviourHandler.mode = mode
        BehaviourHandler.payload = attrs.pop("payload", DATA)
        for key, value in attrs.items():
            setattr(BehaviourHandler, key, value)
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), BehaviourHandler)
        self._srv.daemon_threads = True
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}/file.bin"
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        return False


def _cfg(**kw) -> AppConfig:
    c = AppConfig()
    c.block_private_urls = False
    c.connection_mode = "manual"
    c.num_threads = 4
    c.verify_size = True
    c.max_retries = 15          # the production default: the *budget* must stop us
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _run(url, cfg, tmp: Path, **kwargs):
    """Drive one download and return (ok, controller, progress_events, statuses)."""
    session = SessionManager(cfg)
    analysis = kwargs.pop("analysis", None)
    if analysis is None and kwargs.pop("probe", True):
        analysis = analyze_url(url, cfg, session)
    ctrl = DownloadController(cfg, session, show_progress=False)
    events: list = []
    statuses: list = []
    t0 = time.monotonic()
    ok = ctrl.download_file(
        url, tmp,
        progress_callback=lambda c, t: events.append((time.monotonic() - t0, c, t)),
        control=TaskControl(),
        pre_analysis=analysis,
        status_callback=statuses.append,
    )
    return ok, ctrl, events, statuses


def _verify(path: Path) -> bool:
    return (path.exists()
            and hashlib.sha256(path.read_bytes()).hexdigest()
            == hashlib.sha256(DATA).hexdigest())


class StartupLatencyTest(unittest.TestCase):
    """A slow or silent server must not stall the engine indefinitely."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="n13-startup-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_production_budget_is_bounded(self):
        # Guard against a regression to the old 6 x 15 s ladder (~98 s measured).
        self.assertLessEqual(dl._STARTUP_TOTAL_BUDGET, 25.0)
        self.assertLessEqual(dl._STARTUP_MAX_ATTEMPTS, 6)
        self.assertLessEqual(probe_mod.PROBE_HEAD_READ_TIMEOUT, 5.0)
        self.assertLessEqual(probe_mod.PROBE_TOTAL_BUDGET, 10.0)
        # The budget must never be *shorter* than one read timeout, or the first
        # attempt would be cut off before a slow server could legitimately
        # answer and the retry would restart the wait from zero.
        cfg = _cfg()
        budget = DownloadController(cfg, SessionManager(cfg), show_progress=False)._startup_budget()
        self.assertGreaterEqual(budget, float(cfg.startup_read_timeout))
        self.assertLessEqual(budget, 30.0)

    def test_budget_grows_with_the_read_timeout_setting(self):
        """Raising the user-visible timeout must actually tolerate a slower
        server, instead of being silently defeated by a fixed ceiling."""
        slow = _cfg(startup_read_timeout=40.0)
        fast = _cfg(startup_read_timeout=15.0)
        slow_budget = DownloadController(slow, SessionManager(slow),
                                         show_progress=False)._startup_budget()
        fast_budget = DownloadController(fast, SessionManager(fast),
                                         show_progress=False)._startup_budget()
        self.assertGreater(slow_budget, fast_budget)
        self.assertGreater(slow_budget, 40.0)

    def test_server_that_never_answers_fails_within_the_budget(self):
        with _Server("silent", head_delay=30.0) as srv:
            cfg = _cfg()
            # Shrink the whole phase directly: the budget is derived from the
            # read-timeout setting now, so patching the constant alone would not
            # make it smaller.
            with patch.object(DownloadController, "_startup_budget",
                              lambda self: 4.0):
                t0 = time.monotonic()
                ok, ctrl, _events, _statuses = _run(srv.url, cfg, self.dir)
                elapsed = time.monotonic() - t0
        self.assertFalse(ok)
        self.assertLess(elapsed, 12.0,
                        f"silent server stalled the engine for {elapsed:.1f}s")
        self.assertIn("no data", ctrl.last_error.lower())
        self.assertTrue(ctrl.last_error.strip())

    def test_hanging_head_does_not_delay_a_working_transfer(self):
        # HEAD is ignored/slow; GET is instant.  The transfer must not wait for
        # the full generic read timeout before starting.
        with _Server("head_hang", head_delay=30.0) as srv:
            cfg = _cfg()
            t0 = time.monotonic()
            ok, _ctrl, _events, _statuses = _run(srv.url, cfg, self.dir)
            elapsed = time.monotonic() - t0
        self.assertTrue(ok, "a working GET must still be downloaded")
        self.assertTrue(_verify(self.dir / "file.bin"))
        self.assertLess(elapsed, probe_mod.PROBE_HEAD_READ_TIMEOUT + 6.0,
                        f"HEAD stall cost {elapsed:.1f}s before the transfer")

    def test_probe_is_bounded_when_every_request_hangs(self):
        with _Server("all_silent", head_delay=30.0) as srv:
            cfg = _cfg()
            t0 = time.monotonic()
            analysis = analyze_url(srv.url, cfg, SessionManager(cfg))
            elapsed = time.monotonic() - t0
        self.assertLess(elapsed, probe_mod.PROBE_TOTAL_BUDGET + 3.0,
                        f"probe took {elapsed:.1f}s; it is supposed to be bounded")
        self.assertFalse(analysis.ok)      # nothing to report, and it said so

    def test_head_unsupported_still_downloads_immediately(self):
        with _Server("head_405") as srv:
            cfg = _cfg()
            t0 = time.monotonic()
            ok, _ctrl, _events, _statuses = _run(srv.url, cfg, self.dir)
            elapsed = time.monotonic() - t0
        self.assertTrue(ok)
        self.assertTrue(_verify(self.dir / "file.bin"))
        self.assertLess(elapsed, 5.0, f"405-on-HEAD path took {elapsed:.1f}s")

    def test_dead_probe_result_does_not_block_the_transfer(self):
        # A failed analysis (as the queue's ANALYZING step produces) must fall
        # through to a direct download rather than failing the task.
        with _Server("ok") as srv:
            cfg = _cfg()
            from core.analyzer import Analysis

            failed = Analysis(url=srv.url, ok=False, error="probe said no")
            ok, _ctrl, events, _s = _run(srv.url, cfg, self.dir, analysis=failed)
        self.assertTrue(ok, "a failed probe must not prevent a valid GET")
        self.assertTrue(_verify(self.dir / "file.bin"))
        self.assertTrue(any(c > 0 for _t, c, _tot in events))


class StatusHonestyTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="n13-status-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_connecting_precedes_downloading(self):
        with _Server("ok") as srv:
            ok, _ctrl, _events, statuses = _run(srv.url, _cfg(), self.dir)
        self.assertTrue(ok)
        self.assertIn("CONNECTING", statuses)
        self.assertIn("DOWNLOADING", statuses)
        self.assertLess(statuses.index("CONNECTING"), statuses.index("DOWNLOADING"),
                        "the engine claimed DOWNLOADING before connecting")

    def test_downloading_is_not_announced_before_the_first_byte(self):
        # A silent server must never be reported as DOWNLOADING.
        with _Server("silent", head_delay=30.0) as srv:
            with patch.object(DownloadController, "_startup_budget",
                              lambda self: 3.0):
                ok, _ctrl, _events, statuses = _run(srv.url, _cfg(), self.dir)
        self.assertFalse(ok)
        self.assertIn("CONNECTING", statuses)
        self.assertNotIn("DOWNLOADING", statuses,
                         "a server that never sent a byte was reported as downloading")


class ProgressHonestyTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="n13-progress-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_progress_granularity_is_not_the_read_chunk(self):
        # The read slice *is* the progress granularity (iter_content blocks
        # until it has n bytes).  A multi-megabyte slice is what made the bar
        # sit still and then jump, so the gap between consecutive updates must
        # stay bounded by the slice — a 4 MB read would show a ~3 MB step here.
        self.assertLessEqual(dl._READ_SLICE, 1024 * 1024)
        with _Server("trickle", body_piece=128 * 1024, body_pause=0.05) as srv:
            ok, _ctrl, events, _statuses = _run(srv.url, _cfg(), self.dir)
        self.assertTrue(ok)
        values = [c for _t, c, _tot in events]
        steps = [b - a for a, b in zip(values, values[1:]) if b > a]
        self.assertTrue(steps, "no progress was reported at all")
        self.assertLessEqual(
            max(steps), dl._READ_SLICE * 2,
            f"largest progress jump was {max(steps)} bytes; the read slice is "
            f"{dl._READ_SLICE} — updates are being batched by the read size",
        )

    def test_progress_is_monotonic_and_never_exceeds_received(self):
        with _Server("trickle", body_piece=64 * 1024, body_pause=0.0) as srv:
            ok, _ctrl, events, _statuses = _run(srv.url, _cfg(), self.dir)
        self.assertTrue(ok)
        values = [c for _t, c, _tot in events]
        self.assertEqual(values, sorted(values), f"progress went backwards: {values}")
        self.assertLessEqual(max(values), len(DATA))
        self.assertEqual(values[-1], len(DATA), "final progress must be the full size")

    def test_first_progress_arrives_early_on_a_slow_body(self):
        # ~1 MB/s: with a 4 MB read chunk the first update would only appear
        # after the whole body.  With a bounded slice it must appear well before.
        with _Server("trickle", body_piece=128 * 1024, body_pause=0.02) as srv:
            cfg = _cfg()
            session = SessionManager(cfg)
            analysis = analyze_url(srv.url, cfg, session)
            ctrl = DownloadController(cfg, session, show_progress=False)
            first = {}
            t0 = time.monotonic()

            def cb(completed, _total):
                if completed > 0 and "t" not in first:
                    first["t"] = time.monotonic() - t0
                    first["bytes"] = completed

            ok = ctrl.download_file(srv.url, self.dir, progress_callback=cb,
                                    control=TaskControl(), pre_analysis=analysis)
            total = time.monotonic() - t0
        self.assertTrue(ok)
        self.assertIn("t", first, "no progress was ever reported")
        self.assertLess(first["t"], total * 0.6,
                        f"first update at {first['t']:.2f}s of a {total:.2f}s transfer")
        self.assertLess(first["bytes"], len(DATA))

    def test_unknown_content_length_reports_real_bytes(self):
        with _Server("no_length") as srv:
            ok, _ctrl, events, _statuses = _run(srv.url, _cfg(), self.dir)
        self.assertTrue(ok)
        self.assertTrue(_verify(self.dir / "file.bin"))
        values = [c for _t, c, _tot in events]
        self.assertTrue(values)
        self.assertEqual(values[-1], len(DATA),
                         "with no Content-Length the byte count must still be real")
        self.assertEqual(events[-1][2], 0,
                         "an unknown total must be reported as unknown, not invented")

    def test_multi_part_progress_never_regresses(self):
        """A part's on-disk size LAGS the byte counter.

        Part handles are opened with an 8 MB buffer, so ``downloaded_size``
        (a ``stat()``) can read 0 for a part that has logically written several
        megabytes.  Reconciling the shared counter from disk must therefore only
        ever move it upwards — assigning it outright made the displayed progress
        jump *backwards* whenever one part finished while another still had
        buffered data.  4 parts of ~3 MB each is the shape that exposes it,
        because every part fits inside the write buffer.
        """
        payload = os.urandom(12 * 1024 * 1024 + 4096)
        with _Server("trickle", payload=payload, body_piece=256 * 1024,
                     body_pause=0.01) as srv:
            for run in range(3):
                ok, _ctrl, events, _statuses = _run(srv.url, _cfg(), self.dir)
                self.assertTrue(ok, f"run {run} failed")
                values = [c for _t, c, _tot in events]
                self.assertEqual(values, sorted(values),
                                 f"run {run}: progress went backwards: {values}")
                self.assertEqual(values[-1], len(payload),
                                 f"run {run}: final progress must be the full size")

    def test_progress_survives_a_retry_without_regressing(self):
        # A reset mid-transfer must not rewind the displayed byte count.
        class ResettingHandler(BehaviourHandler):
            resets = 1

            def do_GET(self):
                if ResettingHandler.resets > 0 and self.headers.get("Range") is None:
                    ResettingHandler.resets -= 1
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(DATA)))
                    self.end_headers()
                    try:
                        self.wfile.write(DATA[: len(DATA) // 3])
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    self.connection.close()
                    return
                super().do_GET()

        ResettingHandler.resets = 1
        ResettingHandler.mode = "ok"
        srv = ThreadingHTTPServer(("127.0.0.1", 0), ResettingHandler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/file.bin"
        try:
            ok, _ctrl, events, _statuses = _run(url, _cfg(), self.dir)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertTrue(ok)
        self.assertTrue(_verify(self.dir / "file.bin"))
        values = [c for _t, c, _tot in events]
        self.assertEqual(values, sorted(values), f"progress regressed: {values}")


class ConnectionReuseTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="n13-reuse-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_reconfiguring_the_same_settings_keeps_the_pool(self):
        with _Server("ok") as srv:
            cfg = _cfg()
            session = SessionManager(cfg)
            analysis = analyze_url(srv.url, cfg, session)

            BehaviourHandler.connections = 0
            for i in range(3):
                ctrl = DownloadController(cfg, session, show_progress=False)
                target = self.dir / f"run{i}"
                target.mkdir()
                self.assertTrue(
                    ctrl.download_file(srv.url, target, control=TaskControl(),
                                       pre_analysis=analysis)
                )
            opened = BehaviourHandler.connections
        # 4 parts over 3 sequential downloads: a warm pool means far fewer
        # connections than "a fresh pool per download".
        self.assertLessEqual(
            opened, 6,
            f"opened {opened} TCP connections for 3 downloads — the pool is "
            "being torn down between them",
        )

    def test_a_real_settings_change_does_rebuild_the_pool(self):
        cfg = _cfg()
        session = SessionManager(cfg)
        session.session  # build it
        first = session.session
        changed = cfg.copy()
        changed.user_agent = "N13-Test/9.9"
        session.configure(changed)
        self.assertIsNot(session.session, first,
                         "a changed user-agent must rebuild the transport")


class ConcurrencyAndCleanupTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="n13-conc-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_two_downloads_do_not_starve_each_other(self):
        with _Server("ok") as srv:
            cfg = _cfg(num_threads=4)
            session = SessionManager(cfg)
            analysis = analyze_url(srv.url, cfg, session)
            results = {}

            def one(tag):
                target = self.dir / tag
                target.mkdir()
                ctrl = DownloadController(cfg, session, show_progress=False)
                results[tag] = ctrl.download_file(
                    srv.url, target, control=TaskControl(), pre_analysis=analysis
                )

            threads = [threading.Thread(target=one, args=(f"t{i}",)) for i in range(3)]
            t0 = time.monotonic()
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
            elapsed = time.monotonic() - t0
        self.assertEqual(results, {"t0": True, "t1": True, "t2": True})
        for tag in results:
            self.assertTrue(_verify(self.dir / tag / "file.bin"))
        self.assertLess(elapsed, 45.0, "concurrent downloads were starved")

    def test_disk_write_error_fails_cleanly_and_leaves_no_temp_file(self):
        with _Server("ok") as srv:
            cfg = _cfg()
            session = SessionManager(cfg)
            analysis = analyze_url(srv.url, cfg, session)
            ctrl = DownloadController(cfg, session, show_progress=False)
            import builtins

            real_open = builtins.open
            root = str(self.dir)

            def failing_open(file, mode="r", *args, **kwargs):
                if (str(file).startswith(root)
                        and any(flag in mode for flag in "wax+")):
                    raise OSError(28, "No space left on device")
                return real_open(file, mode, *args, **kwargs)

            with patch("builtins.open", side_effect=failing_open):
                ok = ctrl.download_file(srv.url, self.dir, control=TaskControl(),
                                        pre_analysis=analysis)
        self.assertFalse(ok, "a disk error must not be reported as success")
        self.assertTrue(ctrl.last_error.strip())
        leftovers = [p.name for p in self.dir.iterdir()]
        self.assertEqual(leftovers, [], f"temporary files left behind: {leftovers}")

    def test_unreachable_port_fails_fast(self):
        # A closed port is the cheapest possible failure; it must not be retried
        # for the whole budget.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        url = f"http://127.0.0.1:{port}/file.bin"
        cfg = _cfg()
        t0 = time.monotonic()
        ok, ctrl, _events, _statuses = _run(url, cfg, self.dir)
        elapsed = time.monotonic() - t0
        self.assertFalse(ok)
        self.assertLess(elapsed, dl._STARTUP_TOTAL_BUDGET + 5.0,
                        f"a closed port took {elapsed:.1f}s to fail")
        self.assertTrue(ctrl.last_error.strip())


if __name__ == "__main__":
    unittest.main()
