"""A URL must be saved in the same place however it was submitted.

Regression (browser extension vs direct submission):

* a delivery from the extension supplies **no folder**, so the backend resolved
  the destination through ``category_dirs``;
* the New Download dialog computed ``<download_dir>/<Category>`` itself and
  passed it explicitly.

With a stale ``category_dirs`` entry — a drive letter that is no longer mounted
— the same URL therefore completed when submitted directly and failed when it
arrived from the extension, with nothing to show but "The system cannot find the
path specified".  Both entry points now resolve the destination through one
backend rule, and an override that cannot be created falls back to the download
folder instead of failing the download.
"""

from __future__ import annotations

import hashlib
import os
import shutil
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
from config.settings import AppConfig, directory_anchor_exists
from core.session import SessionManager

PAYLOAD = bytes(range(256)) * (256 * 1024 // 256)
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _unusable_destination() -> str | None:
    """A destination that provably cannot be created on this machine.

    Only a *drive/root* that does not exist is truly impossible (everything
    below it is created on demand), so this looks for an unused drive letter.
    Returns ``None`` on a platform where that cannot be constructed, and the
    affected tests skip rather than pretend.
    """
    if os.name == "nt":
        for letter in "QRSXYZ":
            if not Path(f"{letter}:/").is_dir():
                return f"{letter}:/N13-Missing-Zips"
    return None


UNUSABLE = _unusable_destination()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _head(self, status: int, length: int, start: int = 0) -> None:
        self.send_response(status)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD)-1}/{len(PAYLOAD)}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/x-rar-compressed")
        self.send_header("Content-Disposition", 'attachment; filename="parity.rar"')
        self.send_header("Content-Length", str(length))
        self.end_headers()

    def do_HEAD(self):
        self._head(200, len(PAYLOAD))

    def do_GET(self):
        rng = self.headers.get("Range")
        start = 0
        if rng:
            try:
                start = int(rng.split("=")[1].split("-")[0])
            except (IndexError, ValueError):
                start = 0
        body = PAYLOAD[start:]
        self._head(206 if rng else 200, len(body), start)
        try:
            self.wfile.write(body)
        except OSError:
            pass


class _Server:
    def __enter__(self):
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}/parity.rar"
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        return False


class CategoryFolderFallbackTest(unittest.TestCase):
    """``resolve_category_dir`` never hands out an impossible destination."""

    def test_override_on_a_usable_folder_still_wins(self):
        cfg = AppConfig()
        cfg.download_dir = "C:/Downloads"
        with tempfile.TemporaryDirectory() as real:
            cfg.category_dirs = {"Archives": real}
            self.assertEqual(cfg.resolve_category_dir("Archives", cfg.download_dir), real)

    @unittest.skipUnless(UNUSABLE, "no unused drive letter available")
    def test_override_that_cannot_be_created_falls_back(self):
        cfg = AppConfig()
        base = cfg.download_dir = "C:/Downloads"
        cfg.category_dirs = {"Archives": UNUSABLE}
        self.assertFalse(directory_anchor_exists(UNUSABLE))
        # The automatic routing below the override is what the dialog already
        # used, so the extension path now lands exactly where the direct one does
        # instead of failing before the first byte.
        self.assertEqual(cfg.resolve_category_dir("Archives", base),
                         os.path.join(base, "Archives"))

    def test_relative_and_empty_paths_are_always_creatable(self):
        self.assertTrue(directory_anchor_exists(""))
        self.assertTrue(directory_anchor_exists("relative/folder"))

    def test_existing_anchor_is_creatable(self):
        with tempfile.TemporaryDirectory() as real:
            self.assertTrue(directory_anchor_exists(os.path.join(real, "child", "deep")))


class ExtensionAndDirectDeliveryParityTest(unittest.TestCase):
    """The two real entry points save the same URL in the same folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="n13-parity-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._patches = [
            patch.object(core.paths, "data_dir", return_value=self.tmp / "data"),
            patch.object(core.paths, "config_dir", return_value=self.tmp / "config"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

        from ui.api import Api

        self.cfg = AppConfig()
        self.cfg.auto_start_server = False          # no socket needed
        self.cfg.block_private_urls = False
        self.cfg.connection_mode = "manual"
        self.cfg.num_threads = 2
        self.cfg.max_retries = 2
        self.cfg.retry_delay = 0.01
        self.cfg.retry_max_delay = 0.05
        self.cfg.download_dir = str(self.tmp / "downloads")
        self.cfg.category_dirs = {"Archives": UNUSABLE} if UNUSABLE else {}
        self.api = Api(self.cfg, SessionManager(self.cfg))
        self.addCleanup(self.api.shutdown)

    def _finish(self, seconds: float = 30.0) -> dict:
        """Drain events until the first task reaches a terminal state."""
        deadline = time.monotonic() + seconds
        final: dict = {}
        while time.monotonic() < deadline:
            for evt in self.api.poll_events():
                if evt.get("type") == "task" and (evt.get("task") or {}).get("state") in (
                    "Complete", "Failed", "Cancelled",
                ):
                    final = evt["task"]
            if final:
                return final
            time.sleep(0.02)
        self.fail("the download never reached a terminal state")

    def _payload_ok(self, folder: str) -> bool:
        p = Path(folder) / "parity.rar"
        return p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == SHA

    @unittest.skipUnless(UNUSABLE, "no unused drive letter available")
    def test_extension_delivery_matches_direct_delivery(self):
        with _Server() as srv:
            # ---- the extension path: no folder, backend resolution ------
            self.api._browser_callback(srv.url, autostart=True)
            task = self._finish()
            self.assertEqual(task["state"], "Complete",
                             f"extension delivery failed: {task.get('error')!r}")
            extension_dir = task["directory"]

            # ---- the direct path: dialog-style, folder passed explicitly --
            base = (self.cfg.download_dir or "").rstrip("\\/")
            direct_dir = base + os.sep + "Archives"
            self.api.add_download(srv.url, direct_dir, "parity.rar", "", True,
                                  "Archives", False, "", 0, "")
            task2 = self._finish()
            self.assertEqual(task2["state"], "Complete",
                             f"direct delivery failed: {task2.get('error')!r}")

            self.assertEqual(os.path.normcase(extension_dir),
                             os.path.normcase(task2["directory"]),
                             "the two entry points saved the URL in different folders")
            self.assertEqual(os.path.normcase(extension_dir),
                             os.path.normcase(direct_dir))
            self.assertTrue(self._payload_ok(extension_dir))

    def test_resolve_destination_is_the_single_rule(self):
        """The dialog asks the backend instead of computing the path itself."""
        base = os.path.join(self.cfg.download_dir, "extra")
        res = self.api.resolve_destination(base, "Archives")
        self.assertEqual(res["category"], "Archives")
        self.assertEqual(res["directory"],
                         self.cfg.resolve_category_dir("Archives", base))
        # Defaults come from the settings, so a caller may pass nothing at all.
        self.assertEqual(self.api.resolve_destination("", "")["directory"],
                         self.cfg.resolve_category_dir("General", self.cfg.download_dir))


if __name__ == "__main__":
    unittest.main()
