"""Release hardening — single-instance guard and URL forwarding."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from browser.live_server import LiveServer
from config.settings import AppConfig
from core.session import SessionManager
from core.single_instance import acquire_single_instance, forward_url, release_single_instance


class SingleInstanceTest(unittest.TestCase):
    def tearDown(self):
        release_single_instance()

    def _acquire_in_child(self) -> bool:
        code = (
            "import sys; sys.path.insert(0, r'%s'); "
            "from core.single_instance import acquire_single_instance, release_single_instance; "
            "ok = acquire_single_instance(); "
            "release_single_instance(); "
            "print('ACQUIRED' if ok else 'DENIED')"
        ) % Path(__file__).resolve().parent.parent
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, timeout=30, cwd=Path(__file__).resolve().parent.parent)
        return "ACQUIRED" in proc.stdout

    def test_second_instance_is_denied(self):
        # Parent acquires the mutex first.
        self.assertTrue(acquire_single_instance())
        # A second process must be denied (the parent still holds it).
        self.assertFalse(self._acquire_in_child())

    def test_lock_released_on_exit(self):
        self.assertTrue(acquire_single_instance())
        release_single_instance()
        # After release, a child can acquire it.
        self.assertTrue(self._acquire_in_child())

    def test_forward_url_to_running_relay(self):
        cfg = AppConfig()
        cfg.block_private_urls = False
        cfg.live_server_token = "tok-single"
        # Bind an ephemeral port. The default 6868 can already be held by a
        # running N13 relay, which answers with a different token and makes
        # this test fail for reasons unrelated to forwarding.
        cfg.live_server_port = 0
        delivered = threading.Event()
        received = []

        def _on_url(u, autostart=False):
            received.append(u)
            delivered.set()
            return True

        srv = LiveServer(cfg, SessionManager(cfg), download_callback=_on_url)
        self.assertTrue(srv.start())
        port = srv._server.server_address[1]
        try:
            ok = forward_url(port, cfg.live_server_token, "https://fwd.example/file.zip")
            self.assertTrue(ok)
            # Bounded, deterministic wait on the callback instead of polling
            # a list: a real delivery failure still fails this assertion.
            self.assertTrue(delivered.wait(5), "relay never delivered the URL")
            self.assertEqual(received, ["https://fwd.example/file.zip"])
        finally:
            srv.stop()

    def test_forward_url_rejects_when_no_relay(self):
        # Nothing listening on the port -> forwarding must fail gracefully.
        self.assertFalse(forward_url(59999, "tok", "https://x.example/a.zip"))


if __name__ == "__main__":
    unittest.main()
