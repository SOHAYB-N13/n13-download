"""Release hardening — single-instance guard and URL forwarding."""

from __future__ import annotations

import json
import subprocess
import sys
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
        received = []
        srv = LiveServer(cfg, SessionManager(cfg),
                         download_callback=lambda u, autostart=False: (received.append(u), True)[1])
        self.assertTrue(srv.start())
        port = srv._server.server_address[1]
        try:
            ok = forward_url(port, cfg.live_server_token, "https://fwd.example/file.zip")
            self.assertTrue(ok)
            deadline = time.time() + 5
            while time.time() < deadline and not received:
                time.sleep(0.05)
            self.assertEqual(received, ["https://fwd.example/file.zip"])
        finally:
            srv.stop()

    def test_forward_url_rejects_when_no_relay(self):
        # Nothing listening on the port -> forwarding must fail gracefully.
        self.assertFalse(forward_url(59999, "tok", "https://x.example/a.zip"))


if __name__ == "__main__":
    unittest.main()
