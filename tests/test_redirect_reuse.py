"""Regression tests for STEP 2 — redirect-chain reuse.

The probe resolves redirects once; the transfer must then start at the
resolved (final) URL instead of walking the same chain again.  The resolved
URL is task-scoped and strictly validated (http/https + SSRF).
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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.paths
from config.settings import AppConfig
from core.analyzer import Analysis
from core.download import DownloadController
from core.session import SessionManager
from ui.api import Api

DATA = os.urandom(4 * 1024 * 1024 + 2048)  # ~4 MB


class RedirectHandler(BaseHTTPRequestHandler):
    """/start -> /mid -> /final (configurable hops); /final serves with Range."""

    fail_get = 0          # fail this many GETs to /final with 500
    counts: dict = {}

    def log_message(self, *args):
        pass

    def _count(self, path):
        key = f"{self.server.server_address[1]}{path}"
        RedirectHandler.counts[key] = RedirectHandler.counts.get(key, 0) + 1

    def do_HEAD(self):
        if self.path in ("/start", "/mid"):
            self._count(self.path)
            self.send_response(302)
            self.send_header("Location", "/mid" if self.path == "/start" else "/final")
            self.end_headers()
            return
        self._count(self.path)
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()

    def do_GET(self):
        if self.path in ("/start", "/mid"):
            self._count(self.path)
            self.send_response(302)
            self.send_header("Location", "/mid" if self.path == "/start" else "/final")
            self.end_headers()
            return
        self._count(self.path)
        if RedirectHandler.fail_get > 0:
            RedirectHandler.fail_get -= 1
            self.send_response(500)
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


class PlainHandler(RedirectHandler):
    """No redirects — every path serves the file directly."""

    def do_HEAD(self):
        self._count(self.path)
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()

    def do_GET(self):
        self._count(self.path)
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


class NotFoundHandler(PlainHandler):
    def do_HEAD(self):
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        self.send_response(404)
        self.end_headers()


class Server:
    def __init__(self, handler_cls=RedirectHandler):
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)

    def __enter__(self):
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        port = self._srv.server_address[1]
        self.start_url = f"http://127.0.0.1:{port}/start"
        self.final_url = f"http://127.0.0.1:{port}/final"
        self.plain_url = f"http://127.0.0.1:{port}/file.bin"
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        return False


def reset_counts():
    RedirectHandler.counts = {}
    RedirectHandler.fail_get = 0


class RedirectReuseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AppConfig()
        self.cfg.block_private_urls = False
        self.cfg.verify_size = False
        self.cfg.connection_mode = "manual"
        self.cfg.num_threads = 4
        self.cfg.download_dir = str(self.tmp)
        reset_counts()
        self._data_patch = patch.object(core.paths, "data_dir",
                                        return_value=self.tmp / "data")
        self._data_patch.start()
        self.addCleanup(self._data_patch.stop)

    @staticmethod
    def _port(url):
        return int(url.split(":")[2].split("/")[0])

    def _controller(self, cfg=None):
        cfg = cfg or self.cfg
        return DownloadController(cfg, SessionManager(cfg), show_progress=False)

    def _api(self):
        api = Api(self.cfg, SessionManager(self.cfg))
        api._rules._path = self.tmp / "rules.json"
        self.addCleanup(api.shutdown)
        return api

    def _wait(self, api, tid, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = api.get_download(tid)
            if s and s["state"] in ("Complete", "Failed", "Cancelled"):
                return s
            time.sleep(0.02)
        return api.get_download(tid)

    def test_no_redirect_transfer_uses_original(self):
        with Server(PlainHandler) as srv:
            c = self._controller()
            ok = c.download_file(srv.plain_url, self.tmp / "noredir")
        self.assertTrue(ok)
        self.assertGreater(RedirectHandler.counts.get(
            f"{self._port(srv.plain_url)}/file.bin", 0), 0)

    def test_one_redirect_chain_traversed_once(self):
        with Server() as srv:
            api = self._api()
            res = api.probe_url(srv.start_url)
            self.assertTrue(res["ok"], res.get("error"))
            tid = api.add_download(srv.start_url, autostart=True)
            snap = self._wait(api, tid)
        port = self._port(srv.start_url)
        self.assertEqual(snap["state"], "Complete")
        # The redirect path is hit ONLY by the probe (1 HEAD), never by the
        # transfer parts — the resolved URL is reused.
        self.assertEqual(RedirectHandler.counts.get(f"{port}/start", 0), 1)
        # All part GETs went straight to /final.
        self.assertGreater(RedirectHandler.counts.get(f"{port}/final", 0), 1)

    def test_multi_redirect_chain_traversed_once(self):
        with Server() as srv:
            api = self._api()
            res = api.probe_url(srv.start_url)
            self.assertTrue(res["ok"])
            tid = api.add_download(srv.start_url, autostart=True)
            snap = self._wait(api, tid)
        port = self._port(srv.start_url)
        self.assertEqual(snap["state"], "Complete")
        self.assertEqual(RedirectHandler.counts.get(f"{port}/start", 0), 1)
        self.assertEqual(RedirectHandler.counts.get(f"{port}/mid", 0), 1)
        self.assertGreater(RedirectHandler.counts.get(f"{port}/final", 0), 1)

    def test_redirect_with_range_parts(self):
        with Server() as srv:
            api = self._api()
            res = api.probe_url(srv.start_url)
            self.assertTrue(res["ok"])
            self.assertTrue(res["range"])   # range detected through the chain
            tid = api.add_download(srv.start_url, autostart=True)
            snap = self._wait(api, tid)
        self.assertEqual(snap["state"], "Complete")
        self.assertGreater(snap["connections"], 1)  # multi-part ran

    def test_redirect_with_resume(self):
        cfg = AppConfig()
        cfg.block_private_urls = False
        cfg.verify_size = False
        cfg.connection_mode = "manual"
        cfg.num_threads = 2
        cfg.startup_max_attempts = 2
        cfg.startup_read_timeout = 2
        cfg.retry_delay = 0.01
        with Server() as srv:
            RedirectHandler.fail_get = 50   # all first-run part GETs fail
            c = self._controller(cfg)
            analysis = Analysis(ok=True, url=srv.start_url,
                                probed_at=time.time(),
                                final_url=srv.final_url,
                                total_size=len(DATA),
                                supports_range=True, filename="file.bin")
            first = c.download_file(srv.start_url, self.tmp / "resume",
                                    pre_analysis=analysis)
            self.assertFalse(first)          # parts failed -> state saved
            RedirectHandler.fail_get = 0
            second = c.download_file(srv.start_url, self.tmp / "resume",
                                     pre_analysis=analysis)
        self.assertTrue(second)              # resumed and completed
        self.assertTrue((self.tmp / "resume" / "file.bin").exists())

    def test_parallel_parts_all_use_final_url(self):
        with Server() as srv:
            api = self._api()
            res = api.probe_url(srv.start_url)
            self.assertTrue(res["ok"])
            tid = api.add_download(srv.start_url, autostart=True)
            snap = self._wait(api, tid)
        port = self._port(srv.start_url)
        self.assertEqual(snap["state"], "Complete")
        # NO part GET ever touched the redirect path.
        self.assertEqual(RedirectHandler.counts.get(f"{port}/start", 0), 1)
        self.assertGreater(RedirectHandler.counts.get(f"{port}/final", 0), 2)

    def test_invalid_resolved_url_falls_back(self):
        with Server() as srv:
            c = self._controller()
            ok = c.download_file(
                srv.start_url, self.tmp / "bad",
                pre_analysis=Analysis(
                    ok=True, url=srv.start_url, probed_at=time.time(),
                    final_url="file:///etc/passwd",   # invalid scheme
                    total_size=len(DATA), supports_range=True,
                    filename="file.bin",
                ),
            )
        self.assertTrue(ok)  # fell back to the original -> redirect re-walked
        port = self._port(srv.start_url)
        # The transfer DID traverse the redirect path (fallback behaviour).
        self.assertGreater(RedirectHandler.counts.get(f"{port}/start", 0), 1)

    def test_ssrf_resolved_url_blocked(self):
        # The resolved destination is a private/local host -> must NOT be used.
        with Server() as victim, Server() as srv:
            cfg = AppConfig()
            cfg.block_private_urls = True
            cfg.verify_size = False
            cfg.connection_mode = "manual"
            cfg.num_threads = 1
            c = self._controller(cfg)
            c.download_file(
                srv.plain_url, self.tmp / "ssrf",
                pre_analysis=Analysis(
                    ok=True, url=srv.plain_url, probed_at=time.time(),
                    final_url=victim.plain_url,   # private loopback target
                    total_size=len(DATA), supports_range=True,
                    filename="file.bin",
                ),
            )
        victim_port = self._port(victim.plain_url)
        # The victim server must never have been contacted by the transfer.
        self.assertEqual(RedirectHandler.counts.get(f"{victim_port}/file.bin", 0), 0)

    def test_task_isolation_resolved_urls(self):
        with Server() as srv_a, Server() as srv_b:
            api = self._api()
            res_a = api.probe_url(srv_a.start_url)
            res_b = api.probe_url(srv_b.start_url)
            self.assertTrue(res_a["ok"] and res_b["ok"])
            # Distinct destinations so the two tasks never share .part files.
            tid_a = api.add_download(srv_a.start_url, directory=str(self.tmp / "a"),
                                     autostart=True)
            tid_b = api.add_download(srv_b.start_url, directory=str(self.tmp / "b"),
                                     autostart=True)
            snap_a = self._wait(api, tid_a)
            snap_b = self._wait(api, tid_b)
        port_a = self._port(srv_a.start_url)
        port_b = self._port(srv_b.start_url)
        self.assertEqual(snap_a["state"], "Complete")
        self.assertEqual(snap_b["state"], "Complete")
        # Each download used its OWN final URL: each redirect path was hit
        # only once (by its own probe) and each final served data.
        self.assertEqual(RedirectHandler.counts.get(f"{port_a}/start", 0), 1)
        self.assertEqual(RedirectHandler.counts.get(f"{port_b}/start", 0), 1)
        self.assertGreater(RedirectHandler.counts.get(f"{port_a}/final", 0), 1)
        self.assertGreater(RedirectHandler.counts.get(f"{port_b}/final", 0), 1)

    def test_probe_failure_normal_fallback(self):
        with Server(NotFoundHandler) as srv:
            c = self._controller()
            ok = c.download_file(srv.plain_url, self.tmp / "nf",
                                 pre_analysis=Analysis(ok=False, url=srv.plain_url,
                                                       probed_at=time.time()))
        self.assertFalse(ok)   # probe failed -> direct attempt -> 404 fails
        self.assertIn("404", (c.last_error or ""))


if __name__ == "__main__":
    unittest.main()