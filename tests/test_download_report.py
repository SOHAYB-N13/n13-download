"""The per-download diagnostics record must be accurate, not merely present.

A report that says "verified" for a file nobody verified is worse than no
report at all, so these tests pin the three-valued integrity verdict and the
segment accounting rather than just asserting the object exists.
"""

import hashlib
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core.download import DownloadController, DownloadReport
from core.session import SessionManager
from tests.helpers import TestServer, test_config as make_config

DATA = bytes(range(256)) * 4096          # 1 MiB, non-repeating per block


class _Ranged(BaseHTTPRequestHandler):
    """Honest server: 206 with a correct Content-Range and Content-Length."""

    def log_message(self, *args):
        pass

    def send_response(self, code, message=None):
        self.send_response_only(code, message)

    def _body_for(self, rng):
        if rng and rng.startswith("bytes="):
            first, _, last = rng[6:].partition("-")
            start = int(first)
            end = int(last) if last else len(DATA) - 1
            return start, end, DATA[start:end + 1], 206
        return 0, len(DATA) - 1, DATA, 200

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()

    def do_GET(self):
        start, end, body, code = self._body_for(self.headers.get("Range"))
        self.send_response(code)
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class _NoLength(_Ranged):
    """Never declares a length: the entity is unverifiable by construction."""

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Accept-Ranges", "none")
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(DATA)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


class DownloadReportTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _download(self, handler, **cfg_kw):
        cfg = make_config(**cfg_kw)
        ctl = DownloadController(cfg, SessionManager(cfg), show_progress=False)
        with TestServer(handler) as srv:
            ok = ctl.download_file(srv.url, self.dir)
        return ok, ctl

    def test_a_verified_download_is_reported_as_verified(self):
        ok, ctl = self._download(_Ranged, num_threads=4)
        self.assertTrue(ok)
        report = ctl.last_report
        self.assertIsInstance(report, DownloadReport)
        self.assertEqual(report.integrity, "verified")
        self.assertTrue(report.length_known)
        self.assertEqual(report.expected_length, len(DATA))
        self.assertEqual(report.strategy, "ranged")
        self.assertTrue(report.range_supported)
        self.assertEqual(report.validation, "passed")
        self.assertEqual(report.failure_reason, "")

    def test_segment_accounting_covers_every_byte_exactly_once(self):
        # The 1 MiB fixture needs a small floor to be split at all; with the
        # default min_part_size the planner correctly keeps it as one part.
        ok, ctl = self._download(_Ranged, num_threads=4, min_part_size=64 * 1024)
        self.assertTrue(ok)
        report = ctl.last_report
        self.assertEqual(len(report.segments), 4)
        self.assertEqual(sum(s.expected for s in report.segments), len(DATA))
        self.assertEqual(sum(s.written for s in report.segments), len(DATA))
        # The segments must tile the file: no gap, no overlap.
        covered = 0
        for seg in sorted(report.segments, key=lambda s: s.start):
            self.assertEqual(seg.start, covered, "segment does not start where "
                                                "the previous one ended")
            covered = seg.end + 1
            self.assertTrue(seg.complete)
        self.assertEqual(covered, len(DATA))
        self.assertEqual(report.bytes_written, len(DATA))

    def test_a_download_without_a_declared_length_is_not_called_verified(self):
        ok, ctl = self._download(_NoLength, num_threads=4)
        self.assertTrue(ok, "a length-less entity must still be downloadable")
        report = ctl.last_report
        self.assertFalse(report.length_known)
        self.assertEqual(
            report.integrity, "unverifiable",
            "a file whose length the server never declared cannot be verified",
        )
        saved = self.dir / "file.bin"
        self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(),
                         hashlib.sha256(DATA).hexdigest())

    def test_a_failed_download_reports_failure_and_a_reason(self):
        class _Always500(_Ranged):
            def do_GET(self):
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_HEAD = do_GET

        ok, ctl = self._download(_Always500, num_threads=2, max_retries=1)
        self.assertFalse(ok)
        report = ctl.last_report
        self.assertEqual(report.integrity, "failed")
        self.assertTrue(report.failure_reason, "a failure must say why")

    def test_the_report_never_leaks_a_signed_query_string(self):
        """The record is meant to be safe to attach to a bug report."""
        cfg = make_config(num_threads=2)
        ctl = DownloadController(cfg, SessionManager(cfg), show_progress=False)
        with TestServer(_Ranged) as srv:
            signed = srv.url + "?token=SUPERSECRET&expires=1"
            ctl.download_file(signed, self.dir)
        report = ctl.last_report
        self.assertNotIn("SUPERSECRET", report.to_dict()["url"])
        self.assertNotIn("SUPERSECRET", str(report.to_dict()))
        self.assertEqual(report.host, "127.0.0.1")

    def test_to_dict_is_json_serialisable(self):
        import json
        ok, ctl = self._download(_Ranged, num_threads=2)
        self.assertTrue(ok)
        payload = json.dumps(ctl.last_report.to_dict())
        self.assertIn('"integrity": "verified"', payload)


if __name__ == "__main__":
    unittest.main()
