"""Regression tests for the duplicate-probe elimination fast path.

The GUI dialog probes a URL (API.probeUrl) before the user clicks Add; the
queue's ANALYZING step used to probe the SAME URL again.  These tests prove
the hand-off reuses the UI probe result when valid and falls back to the
normal probe otherwise.
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

import core.analyzer
import core.paths
from config.settings import AppConfig
from core.analyzer import Analysis
from core.session import SessionManager
from ui.api import Api

DATA = os.urandom(512 * 1024 + 100)  # ~0.5 MB


class Handler(BaseHTTPRequestHandler):
    mode = "ok"      # "ok" | "404"

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        if Handler.mode == "404":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()

    def do_GET(self):
        if Handler.mode == "404":
            self.send_response(404)
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


class Server:
    """Context manager around one local HTTP server."""

    def __enter__(self):
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}/file.bin"
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        return False


class ProbeCounter:
    """Counts calls to core.analyzer.analyze_url (the ANALYZING probe)."""

    def __init__(self):
        self.calls = 0
        self._real = core.analyzer.analyze_url

    def __call__(self, url, config, session_manager, *args, **kwargs):
        self.calls += 1
        return self._real(url, config, session_manager, *args, **kwargs)


class ProbeHandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AppConfig()
        self.cfg.block_private_urls = False
        self.cfg.verify_size = False
        # Keep the real files this test downloads out of the user's Downloads
        # folder.  Without this the merge step writes (and then deletes)
        # `file (N).bin.part*` straight into the user's personal directory,
        # which is both a hygiene problem and something an OS-level delete
        # guard will legitimately refuse.
        self.cfg.download_dir = str(self.tmp / "downloads")
        self._data_patch = patch.object(core.paths, "data_dir",
                                        return_value=self.tmp / "data")
        self._data_patch.start()
        self.addCleanup(self._data_patch.stop)
        self.counter = ProbeCounter()
        self._probe_patch = patch.object(core.analyzer, "analyze_url", self.counter)
        self._probe_patch.start()
        self.addCleanup(self._probe_patch.stop)
        self.api = Api(self.cfg, SessionManager(self.cfg))
        self.api._rules._path = self.tmp / "rules.json"
        self.addCleanup(self.api.shutdown)

    def _wait(self, tid, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = self.api.get_download(tid)
            if s and s["state"] in ("Complete", "Failed", "Cancelled"):
                return s
            time.sleep(0.02)
        return self.api.get_download(tid)

    def test_ui_probe_result_is_reused_no_second_probe(self):
        with Server() as srv:
            res = self.api.probe_url(srv.url)
            self.counter.calls = 0   # the UI probe itself is not the analyzer probe
            self.assertTrue(res["ok"], res.get("error"))
            tid = self.api.add_download(srv.url, autostart=True)
            snap = self._wait(tid)
        # The ANALYZING step must NOT probe again — the hand-off was used.
        self.assertEqual(self.counter.calls, 0)
        self.assertEqual(snap["state"], "Complete")
        self.assertEqual(snap["filename"], "file.bin")
        self.assertTrue(snap["total"] > 0)

    def test_failed_ui_probe_falls_back_to_normal_probe(self):
        Handler.mode = "404"
        try:
            with Server() as srv:
                res = self.api.probe_url(srv.url)
                self.counter.calls = 0   # the UI probe itself is not the analyzer probe
                self.assertFalse(res["ok"])
                tid = self.api.add_download(srv.url, autostart=True)
                snap = self._wait(tid)
        finally:
            Handler.mode = "ok"
        # The analyzer performed its normal probe (and the download failed
        # because the server really is 404).
        self.assertEqual(self.counter.calls, 1)
        self.assertIn(snap["state"], ("Failed", "Cancelled"))

    def test_stale_handoff_falls_back_to_normal_probe(self):
        with Server() as srv:
            self.api.probe_url(srv.url)  # populate cache
            self.counter.calls = 0
            # Age the entry beyond the TTL.
            key = next(iter(self.api._probe_cache))
            stamp, analysis = self.api._probe_cache[key]
            self.api._probe_cache[key] = (time.time() - 9999, analysis)
            tid = self.api.add_download(srv.url, autostart=True)
            snap = self._wait(tid)
        self.assertEqual(self.counter.calls, 1)  # stale -> re-probed
        self.assertEqual(snap["state"], "Complete")

    def test_mismatched_url_handoff_falls_back(self):
        with Server() as srv:
            self.api.probe_url(srv.url)
            self.counter.calls = 0
            key = next(iter(self.api._probe_cache))
            wrong = Analysis(ok=True, url="https://other.example/x.bin",
                             probed_at=time.time(), total_size=123)
            self.api._probe_cache[key] = (time.time(), wrong)
            tid = self.api.add_download(srv.url, autostart=True)
            snap = self._wait(tid)
        self.assertEqual(self.counter.calls, 1)  # URL mismatch -> re-probed
        self.assertEqual(snap["state"], "Complete")

    def test_invalid_handoff_falls_back(self):
        with Server() as srv:
            self.api.probe_url(srv.url)
            self.counter.calls = 0
            key = next(iter(self.api._probe_cache))
            self.api._probe_cache[key] = (time.time(), Analysis(ok=False))
            tid = self.api.add_download(srv.url, autostart=True)
            snap = self._wait(tid)
        self.assertEqual(self.counter.calls, 1)  # invalid -> re-probed
        self.assertEqual(snap["state"], "Complete")

    def test_tasks_cannot_reuse_each_others_probe(self):
        """Probing URL A must never serve as metadata for URL B."""
        with Server() as srv_a, Server() as srv_b:
            self.api.probe_url(srv_a.url)
            self.counter.calls = 0   # A's UI probe is not the analyzer probe
            tid = self.api.add_download(srv_b.url, autostart=True)
            snap = self._wait(tid)
        # B was never probed by the UI, so the analyzer probed it itself.
        self.assertEqual(self.counter.calls, 1)
        self.assertEqual(snap["state"], "Complete")
        self.assertEqual(snap["filename"], "file.bin")

    def test_no_ui_probe_normal_path_unchanged(self):
        with Server() as srv:
            tid = self.api.add_download(srv.url, autostart=True)
            snap = self._wait(tid)
        self.assertEqual(self.counter.calls, 1)  # analyzer probed as before
        self.assertEqual(snap["state"], "Complete")
        self.assertEqual(snap["filename"], "file.bin")


if __name__ == "__main__":
    unittest.main()