"""Diagnostics: actionable errors, honoured Retry-After, no leaked secrets.

An error report has to be useful without being dangerous.  These tests pin the
three properties that matter: the retry policy distinguishes permanent from
transient failures, a server's ``Retry-After`` is obeyed but capped, and a URL
written to a log loses its query string (where a signed link keeps its
authorisation).
"""

from __future__ import annotations

import sys
import time
import unittest
from email.utils import formatdate
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from config.settings import AppConfig
from core.download import _RETRY_AFTER_CAP, _retry_after_seconds, _retry_delay
from core.errors import BlockedURLError, friendly_error_message, redact_secrets


class RedactSecretsTest(unittest.TestCase):
    def test_absolute_url_query_is_removed(self):
        cleaned = redact_secrets("https://cdn.example.com/f.zip?X-Amz-Signature=SECRET")
        self.assertEqual(cleaned, "https://cdn.example.com/f.zip?<redacted>")
        self.assertNotIn("SECRET", cleaned)

    def test_relative_url_after_label_is_removed(self):
        # The shape urllib3 actually emits.
        cleaned = redact_secrets(
            "Max retries exceeded with url: /f.zip?token=SECRET&e=99 (Caused by ...)"
        )
        self.assertNotIn("SECRET", cleaned)
        self.assertIn("/f.zip?<redacted>", cleaned)

    def test_embedded_credentials_are_removed(self):
        cleaned = redact_secrets("https://user:hunter2@example.com/f.zip")
        self.assertNotIn("hunter2", cleaned)
        self.assertEqual(cleaned, "https://example.com/f.zip")

    def test_url_without_query_is_untouched(self):
        self.assertEqual(
            redact_secrets("https://example.com/a/b.zip"),
            "https://example.com/a/b.zip",
        )

    def test_plain_text_is_untouched(self):
        self.assertEqual(redact_secrets("connection reset by peer"), "connection reset by peer")
        self.assertEqual(redact_secrets(""), "")

    def test_every_url_in_a_string_is_scrubbed(self):
        cleaned = redact_secrets("https://a.com/1?t=SECRET and https://b.com/2?k=SECRET2")
        self.assertNotIn("SECRET", cleaned)
        self.assertNotIn("SECRET2", cleaned)


def _http_error(status: int, headers: dict | None = None) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    return requests.HTTPError(f"HTTP {status}", response=response)


class RetryAfterTest(unittest.TestCase):
    def test_delta_seconds_form(self):
        self.assertEqual(_retry_after_seconds(_http_error(429, {"Retry-After": "12"})), 12.0)

    def test_http_date_form(self):
        when = formatdate(time.time() + 30, usegmt=True)
        value = _retry_after_seconds(_http_error(503, {"Retry-After": when}))
        self.assertIsNotNone(value)
        self.assertGreater(value, 20.0)
        self.assertLessEqual(value, 31.0)

    def test_absent_header(self):
        self.assertIsNone(_retry_after_seconds(_http_error(503)))
        self.assertIsNone(_retry_after_seconds(requests.ConnectionError("reset")))

    def test_garbage_header_is_ignored(self):
        self.assertIsNone(_retry_after_seconds(_http_error(429, {"Retry-After": "soon"})))


class RetryDelayTest(unittest.TestCase):
    def setUp(self):
        self.cfg = AppConfig()
        self.cfg.retry_delay = 1.0
        self.cfg.retry_backoff = 2.0
        self.cfg.retry_max_delay = 120.0
        self.cfg.retry_jitter = 0.0

    def test_exponential_backoff(self):
        self.assertEqual(_retry_delay(1, self.cfg, started=True), 1.0)
        self.assertEqual(_retry_delay(2, self.cfg, started=True), 2.0)
        self.assertEqual(_retry_delay(3, self.cfg, started=True), 4.0)

    def test_backoff_is_capped(self):
        self.assertEqual(_retry_delay(50, self.cfg, started=True), self.cfg.retry_max_delay)

    def test_retry_after_raises_the_delay(self):
        self.assertEqual(_retry_delay(1, self.cfg, started=True, retry_after=30.0), 30.0)

    def test_retry_after_is_capped(self):
        self.assertEqual(
            _retry_delay(1, self.cfg, started=True, retry_after=10_000.0),
            _RETRY_AFTER_CAP,
        )

    def test_retry_after_never_shortens_the_backoff(self):
        # A server asking for 0 must not make us retry faster than our own
        # backoff already does.
        self.assertEqual(_retry_delay(4, self.cfg, started=True, retry_after=0.0), 8.0)

    def test_startup_attempts_use_the_fast_schedule(self):
        delay = _retry_delay(1, self.cfg, started=False)
        self.assertLessEqual(delay, 2.0)


class FriendlyMessageTest(unittest.TestCase):
    def test_status_messages_are_actionable(self):
        self.assertIn("404", friendly_error_message(_http_error(404)))
        self.assertIn("401", friendly_error_message(_http_error(401)))
        self.assertIn("retry", friendly_error_message(_http_error(503)).lower())
        self.assertIn("retry", friendly_error_message(_http_error(429)).lower())

    def test_network_messages(self):
        self.assertIn("timed out", friendly_error_message(requests.Timeout("x")).lower())
        self.assertIn(
            "connection", friendly_error_message(requests.ConnectionError("x")).lower()
        )

    def test_policy_block_is_named(self):
        message = friendly_error_message(BlockedURLError("Blocked IP address"))
        self.assertIn("security policy", message)
        self.assertIn("Blocked IP address", message)

    def test_never_leaks_a_traceback(self):
        message = friendly_error_message(ValueError("boom"))
        self.assertNotIn("Traceback", message)


if __name__ == "__main__":
    unittest.main()
