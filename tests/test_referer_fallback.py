"""Referer handling: required by some hosts, rejected by others.

Both behaviours exist in the wild, so the header set has to be decided per host
and only after a rejection has been observed.  These tests pin all three parts:

* the builder honours ``omit_referer`` and still sends a Referer by default;
* the session remembers a host that refused one, and does not blacklist others;
* an end-to-end download from a Referer-rejecting server succeeds, and proves
  the fallback was actually used rather than the header never being sent.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.download import DownloadController
from core.session import SessionManager
from core.utils import best_referer_for, build_browser_headers
from tests.helpers import (
    DATA,
    RefererGateHandler,
    TestServer,
    test_config as _test_config,
)


class HeaderBuilderTest(unittest.TestCase):
    def test_referer_is_sent_by_default(self):
        headers = build_browser_headers("https://example.com/files/a.bin", "UA")
        self.assertEqual(headers.get("Referer"), "https://example.com/")
        self.assertEqual(headers.get("Origin"), "https://example.com")

    def test_omit_referer_drops_both_headers(self):
        headers = build_browser_headers(
            "https://example.com/files/a.bin", "UA", omit_referer=True
        )
        self.assertNotIn("Referer", headers)
        self.assertNotIn("Origin", headers)
        # The rest of the browser-like set must survive: hosts that reject a
        # Referer still expect a normal client.
        self.assertEqual(headers["User-Agent"], "UA")
        self.assertEqual(headers["Accept-Encoding"], "identity")

    def test_known_gated_hosts_keep_their_specific_referer(self):
        self.assertEqual(
            best_referer_for("https://download.nvidia.com/x.exe"),
            "https://www.nvidia.com/",
        )
        headers = build_browser_headers(
            "https://download.nvidia.com/x.exe", "UA", omit_referer=False
        )
        self.assertEqual(headers["Referer"], "https://www.nvidia.com/")


class SessionPolicyTest(unittest.TestCase):
    def test_policy_is_per_host(self):
        manager = SessionManager(_test_config())
        a = "https://rejecter.example.com/f.bin"
        b = "https://other.example.com/f.bin"
        self.assertFalse(manager.should_omit_referer(a))
        self.assertTrue(manager.mark_no_referer(a))
        self.assertTrue(manager.should_omit_referer(a))
        # Marking is idempotent and does not leak to another host.
        self.assertFalse(manager.mark_no_referer(a))
        self.assertFalse(manager.should_omit_referer(b))

    def test_unknown_host_is_not_marked(self):
        manager = SessionManager(_test_config())
        self.assertFalse(manager.should_omit_referer("not a url"))


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_download_from_a_referer_rejecting_server(self):
        with TestServer(RefererGateHandler) as srv:
            cfg = _test_config(num_threads=4)
            controller = DownloadController(
                cfg, SessionManager(cfg), show_progress=False
            )
            ok = controller.download_file(srv.url, self.dir)
            self.assertTrue(ok, controller.last_error)
            saved = self.dir / "file.bin"
            self.assertEqual(saved.stat().st_size, len(DATA))
            self.assertEqual(
                hashlib.sha256(saved.read_bytes()).hexdigest(),
                hashlib.sha256(DATA).hexdigest(),
            )
            # The gate must have refused at least one request, otherwise this
            # test would pass on a build that never sent a Referer at all.
            self.assertGreater(
                RefererGateHandler.rejected, 0,
                "no request carried a Referer, so the fallback was not exercised",
            )

    def test_fallback_costs_one_extra_request_not_a_retry_storm(self):
        with TestServer(RefererGateHandler) as srv:
            cfg = _test_config(num_threads=4)
            controller = DownloadController(
                cfg, SessionManager(cfg), show_progress=False
            )
            self.assertTrue(controller.download_file(srv.url, self.dir))
            report = controller.last_report
            # The probe is refused once; the host is then marked for the whole
            # download, so parts must not repeat the mistake.
            self.assertLessEqual(
                RefererGateHandler.rejected, 3,
                "the fallback was rediscovered per part instead of once per host",
            )
            self.assertIsNotNone(report)


if __name__ == "__main__":
    unittest.main()
