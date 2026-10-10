"""An extension delivery must be queued, and every outcome must be visible.

The browser extension is the one entry point with no dialog and no console: it
POSTs a URL to the Live Server, the server hands it to
``Api._browser_callback``, and from then on nothing is on screen.  That is what
made the reported "the extension sent the link but the download failed" hard to
act on — the application log recorded only a duration, and a link that could not
be queued (an invalid URL, a duplicate, an exception while adding) was either
dropped or, worse, downloaded by the Live Server itself, outside the queue and
outside the duplicate policy.

These tests pin the runtime contract:

* a delivered link is logged and becomes a task with the same destination the
  New Download dialog would use;
* a delivery that cannot be queued is reported — logged and raised as a toast —
  and never silently handled elsewhere;
* a duplicate delivery is reported as a duplicate instead of looking like a new
  download;
* the Live Server logs both what it accepted and what it rejected, with the
  reason.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.paths
from config.settings import AppConfig
from core.session import SessionManager

URL = "https://cdn.example.com/archives/big.rar"


def _drain(api) -> list:
    return list(api.poll_events())


class BrowserDeliveryTest(unittest.TestCase):
    """``Api._browser_callback`` — the extension's way into the application."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="n13-browser-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for target in ("data_dir", "config_dir"):
            p = patch.object(core.paths, target, return_value=self.tmp / target)
            p.start()
            self.addCleanup(p.stop)

        from ui.api import Api

        self.cfg = AppConfig()
        self.cfg.auto_start_server = False          # no socket needed here
        self.cfg.download_dir = str(self.tmp / "downloads")
        self.cfg.category_dirs = {}
        self.cfg.auto_categorize = True
        self.cfg.duplicate_policy = "ask"
        self.api = Api(self.cfg, SessionManager(self.cfg))
        self.addCleanup(self.api.shutdown)

    def _toasts(self, events: list) -> list:
        return [e for e in events if e.get("type") == "toast"]

    def test_delivery_is_logged_and_queued(self):
        with self.assertLogs("n13", level="INFO") as logs:
            self.api._browser_callback(URL, autostart=True)
        self.assertTrue(any("Extension link queued as task" in m for m in logs.output),
                        logs.output)

        tasks = self.api.get_downloads()
        self.assertEqual(len(tasks), 1, tasks)
        self.assertEqual(tasks[0]["url"], URL)
        # The deliverable: the extension's destination is the one the dialog
        # resolves too, not a second rule of its own.
        self.assertEqual(
            os.path.normcase(tasks[0]["directory"]),
            os.path.normcase(self.api.resolve_destination("", tasks[0]["category"])["directory"]),
        )
        toasts = self._toasts(self.api.poll_events())
        self.assertTrue(toasts, "the user was given no feedback for a delivery")
        self.assertEqual(toasts[0].get("kind", "info"), "info")

    def test_duplicate_delivery_is_reported_as_a_duplicate(self):
        self.api._browser_callback(URL, autostart=True)
        first = self.api.get_downloads()[0]["id"]
        _drain(self.api)

        with self.assertLogs("n13", level="INFO") as logs:
            self.api._browser_callback(URL, autostart=True)
        self.assertTrue(any("already in the queue" in m for m in logs.output),
                        logs.output)
        tasks = self.api.get_downloads()
        self.assertEqual([t["id"] for t in tasks], [first],
                         "a duplicate delivery must not create a second task")
        self.assertTrue(self._toasts(self.api.poll_events()),
                        "a duplicate delivery must still tell the user something")

    def test_unqueueable_delivery_is_reported_and_still_handled(self):
        """A failure must not be left for the Live Server to act on.

        ``False``/raise means "not handled" to the Live Server, which then
        downloads the URL itself — outside the queue, the rules and the
        duplicate policy.  So the callback reports the failure and returns True.
        """
        with patch.object(self.api, "add_download", side_effect=RuntimeError("disk offline")):
            with self.assertLogs("n13", level="ERROR") as logs:
                handled = self.api._browser_callback(URL, autostart=True)
        self.assertTrue(handled, "the delivery must not be handed back to the server")
        self.assertTrue(any("could not be queued" in m for m in logs.output), logs.output)
        self.assertEqual(self.api.get_downloads(), [])
        toasts = self._toasts(self.api.poll_events())
        self.assertEqual(len(toasts), 1, toasts)
        self.assertEqual(toasts[0]["kind"], "error")
        self.assertTrue(toasts[0]["message"].strip())

    def test_invalid_url_is_rejected_visibly(self):
        with self.assertLogs("n13", level="WARNING") as logs:
            handled = self.api._browser_callback("not-a-url", autostart=True)
        self.assertTrue(handled)
        self.assertTrue(any("rejected" in m for m in logs.output), logs.output)
        self.assertEqual(self.api.get_downloads(), [])
        toasts = self._toasts(self.api.poll_events())
        self.assertEqual(len(toasts), 1, toasts)
        self.assertEqual(toasts[0]["kind"], "error")

    def test_cool_delivery_offers_the_link_to_the_ui_without_downloading(self):
        with self.assertLogs("n13", level="INFO"):
            self.api._browser_callback(URL, autostart=False)
        events = self.api.poll_events()
        self.assertTrue(any(e.get("type") == "browser_url" and e.get("url") == URL
                            for e in events), events)
        self.assertEqual(self.api.get_downloads(), [],
                         "a cool delivery must not start on its own")


class LiveServerLoggingTest(unittest.TestCase):
    """The Live Server records what arrived, and why anything was refused."""

    def setUp(self):
        from browser.live_server import LiveServer

        self.cfg = AppConfig()
        self.cfg.live_server_token = "t" * 32
        self.cfg.block_private_urls = False
        self.server = LiveServer(self.cfg, SessionManager(self.cfg))

    def test_accepted_and_rejected_links_are_logged_with_reasons(self):
        with self.assertLogs("n13", level="INFO") as logs:
            accepted, rejected = self.server._validate_and_queue(
                [URL, "", "ftp://cdn.example.com/payload.bin"], autostart=True
            )
        self.assertEqual((accepted, rejected), (1, 2), logs.output)
        text = "\n".join(logs.output)
        self.assertIn("Link accepted (autostart=True)", text)
        self.assertIn("cdn.example.com/archives/big.rar", text)
        self.assertIn("payload.bin", text)
        # The reason for each refusal is in the log, not only on a console the
        # packaged GUI does not have.
        self.assertIn("Link rejected", text)
        self.assertIn("empty after normalisation", text)

    def test_no_secret_query_is_written_to_the_log(self):
        with self.assertLogs("n13", level="INFO") as logs:
            self.server._validate_and_queue(
                ["https://cdn.example.com/big.rar?token=SUPERSECRET"], autostart=True
            )
        text = "\n".join(logs.output)
        self.assertNotIn("SUPERSECRET", text)
        self.assertIn("<redacted>", text)


class FailureReasonLoggingTest(unittest.TestCase):
    """A failed download explains itself in one line."""

    def setUp(self):
        from core.download import DownloadController, DownloadTimeline

        self.cfg = AppConfig()
        self.controller = DownloadController(
            self.cfg, SessionManager(self.cfg), lambda *a, **k: None, show_progress=False
        )
        self.controller._timeline = DownloadTimeline()

    def test_failure_line_carries_the_reason(self):
        self.controller.last_error = "Connection error: cannot reach server"
        with self.assertLogs("n13", level="INFO") as logs:
            self.controller._log_timeline("https://cdn.example.com/big.rar", "failed")
        text = "\n".join(logs.output)
        self.assertIn("cannot reach server", text)
        self.assertIn("download failed", text)

    def test_success_line_stays_short_and_redacts_the_url(self):
        with self.assertLogs("n13", level="INFO") as logs:
            self.controller._log_timeline(
                "https://cdn.example.com/big.rar?token=SUPERSECRET", "ok"
            )
        text = "\n".join(logs.output)
        self.assertNotIn("SUPERSECRET", text)
        self.assertIn("download ok", text)


if __name__ == "__main__":
    unittest.main()
