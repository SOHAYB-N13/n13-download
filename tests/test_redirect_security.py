"""Redirect hops must pass the same SSRF policy as the URL the user supplied.

A public link that 302s to ``169.254.169.254`` (cloud metadata) or
``127.0.0.1`` is the classic SSRF bypass: validating only the input URL misses
it entirely.  These tests pin the guard that closes it, and pin the two things
that must *not* change — the caller's own URL is never re-validated (so the
probe and the transfer keep working, and no false positive is invented), and a
redirect loop is a permanent error rather than something to retry 15 times.
"""

from __future__ import annotations

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.session as session_mod
import requests
from config.settings import AppConfig
from core.download import _is_retryable_exception
from core.errors import BlockedURLError, friendly_error_message
from core.security import check_redirect_target
from core.session import SessionManager

# A link-local metadata address: private, and reachable from nowhere in tests.
METADATA_URL = "http://169.254.169.254/latest/meta-data/"


class HopHandler(BaseHTTPRequestHandler):
    """Serves a configurable redirect map; any unmapped path returns 200."""

    hops: dict = {}
    seen: list = []

    def log_message(self, *args):
        pass

    def _handle(self):
        HopHandler.seen.append(self.path)
        target = HopHandler.hops.get(self.path)
        if target:
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _handle
    do_HEAD = _handle


class LoopHandler(BaseHTTPRequestHandler):
    """Redirects to itself forever."""

    hits: int = 0

    def log_message(self, *args):
        pass

    def _handle(self):
        LoopHandler.hits += 1
        self.send_response(302)
        self.send_header("Location", "/loop")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = _handle
    do_HEAD = _handle


class _Server:
    def __init__(self, handler_cls):
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        self.base = f"http://127.0.0.1:{self._srv.server_address[1]}"
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        return False


def _session(block_private: bool) -> requests.Session:
    cfg = AppConfig()
    cfg.block_private_urls = block_private
    return SessionManager(cfg).session


class RedirectGuardTest(unittest.TestCase):
    def setUp(self):
        HopHandler.hops = {}
        HopHandler.seen = []
        LoopHandler.hits = 0

    def test_redirect_to_private_target_is_blocked(self):
        HopHandler.hops = {"/start": METADATA_URL}
        with _Server(HopHandler) as srv:
            sess = _session(block_private=True)
            with self.assertRaises(BlockedURLError) as ctx:
                sess.get(srv.base + "/start", timeout=5)
            self.assertIn("Blocked", ctx.exception.reason)
            self.assertEqual(HopHandler.seen, ["/start"])

    def test_guard_is_off_when_the_policy_is_off(self):
        # A same-host redirect is followed normally, and the guard is not even
        # consulted: turning the policy off must not leave a half-applied check.
        HopHandler.hops = {"/start": "/plain"}
        calls: list = []
        with _Server(HopHandler) as srv:
            sess = _session(block_private=False)
            with patch.object(
                session_mod,
                "check_redirect_target",
                side_effect=lambda *a, **k: calls.append(a) or (True, ""),
            ):
                resp = sess.get(srv.base + "/start", timeout=5)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.text, "ok")
            self.assertEqual(calls, [])
            self.assertEqual(HopHandler.seen, ["/start", "/plain"])

    def test_guard_is_active_when_the_policy_is_on(self):
        # The same hop is refused once the policy is on — the pairing that shows
        # the guard is what makes the difference, not the redirect itself.
        HopHandler.hops = {"/start": "/plain"}
        with _Server(HopHandler) as srv:
            sess = _session(block_private=True)
            with self.assertRaises(BlockedURLError):
                sess.get(srv.base + "/start", timeout=5)
            self.assertEqual(HopHandler.seen, ["/start"])

    def test_redirect_to_plain_private_ip_is_blocked(self):
        HopHandler.hops = {"/start": "http://10.0.0.7/secret.zip"}
        with _Server(HopHandler) as srv:
            sess = _session(block_private=True)
            with self.assertRaises(BlockedURLError) as ctx:
                sess.get(srv.base + "/start", timeout=5)
            self.assertIn("Blocked", ctx.exception.reason)
            self.assertEqual(HopHandler.seen, ["/start"])

    def test_guard_checks_every_hop_not_just_the_first(self):
        # /start -> /mid -> /blocked.  Only the third URL is forbidden, so a
        # guard that inspected just the first redirect would let it through.
        HopHandler.hops = {"/start": "/mid", "/mid": "/blocked"}

        def fake_check(url: str, block_private: bool = True):
            if url.endswith("/blocked"):
                return False, "Blocked hostname"
            return True, ""

        with _Server(HopHandler) as srv:
            sess = _session(block_private=True)
            with patch.object(session_mod, "check_redirect_target", side_effect=fake_check):
                with self.assertRaises(BlockedURLError) as ctx:
                    sess.get(srv.base + "/start", timeout=5)
            self.assertEqual(ctx.exception.reason, "Blocked hostname")
            # /mid was requested (the chain really was followed), /blocked was
            # not (the guard stopped it before dispatch).
            self.assertEqual(HopHandler.seen, ["/start", "/mid"])

    def test_entry_url_is_exempt_from_the_guard(self):
        # The caller's own URL is validated by the probe/transfer layer.  If the
        # guard re-checked it, every local or already-approved request would be
        # refused — a false positive that would break the downloader.
        with _Server(HopHandler) as srv:
            sess = _session(block_private=True)
            resp = sess.get(srv.base + "/plain", timeout=5)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.text, "ok")

    def test_redirect_loop_is_bounded(self):
        with _Server(LoopHandler) as srv:
            sess = _session(block_private=False)
            with self.assertRaises(requests.TooManyRedirects):
                sess.get(srv.base + "/loop", timeout=5)
            # requests' own cap stops it; the loop cannot run forever.
            self.assertLessEqual(LoopHandler.hits, 40)


class RedirectPolicyUnitTest(unittest.TestCase):
    def test_private_and_non_http_targets_are_refused(self):
        for url in (
            "http://127.0.0.1:6868/",
            "http://169.254.169.254/",
            "http://10.0.0.5/file.zip",
            "http://[::1]/file.zip",
            "file:///etc/passwd",
            "ftp://example.com/file.zip",
            "http://user:pw@example.com/file.zip",
        ):
            with self.subTest(url=url):
                allowed, reason = check_redirect_target(url, block_private=True)
                self.assertFalse(allowed)
                self.assertTrue(reason)

    def test_public_targets_are_allowed(self):
        allowed, reason = check_redirect_target("https://example.com/file.zip", block_private=True)
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_dns_failure_is_not_reported_as_a_block(self):
        # A hostname that cannot resolve is a network problem, not a policy
        # violation: the transfer layer must be allowed to surface it (and
        # retry it) instead of failing the download permanently.
        allowed, reason = check_redirect_target("http://nonexistent.invalid/file.zip")
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_policy_can_be_disabled(self):
        allowed, _ = check_redirect_target("http://127.0.0.1:1/", block_private=False)
        self.assertTrue(allowed)


class BlockedErrorClassificationTest(unittest.TestCase):
    def test_blocked_url_error_is_never_retried(self):
        retryable, status = _is_retryable_exception(BlockedURLError("Blocked IP address"))
        self.assertFalse(retryable)
        self.assertIsNone(status)

    def test_permanent_url_errors_are_never_retried(self):
        for exc in (
            requests.TooManyRedirects("too many"),
            requests.exceptions.MissingSchema("no scheme"),
            requests.exceptions.InvalidURL("bad"),
        ):
            with self.subTest(exc=type(exc).__name__):
                retryable, _ = _is_retryable_exception(exc)
                self.assertFalse(retryable)

    def test_transient_errors_are_still_retried(self):
        for exc in (requests.ConnectionError("reset"), requests.Timeout("slow")):
            with self.subTest(exc=type(exc).__name__):
                retryable, _ = _is_retryable_exception(exc)
                self.assertTrue(retryable)

    def test_friendly_message_names_the_policy(self):
        msg = friendly_error_message(BlockedURLError("Blocked IP address"))
        self.assertIn("security policy", msg)
        self.assertIn("Blocked IP address", msg)


if __name__ == "__main__":
    unittest.main()
