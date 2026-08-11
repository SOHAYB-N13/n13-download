"""Phase J — live-server batch endpoint + auth regression."""

from __future__ import annotations

import json
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


class LiveServerTest(unittest.TestCase):
    def _server(self):
        cfg = AppConfig()
        cfg.block_private_urls = False
        cfg.live_server_token = "tok-123"
        received = []
        srv = LiveServer(cfg, SessionManager(cfg),
                         download_callback=lambda u, autostart=False: (received.append(u), True)[1])
        self.assertTrue(srv.start())
        self.port = srv._server.server_address[1]
        return srv, received

    def _post(self, path, payload, token=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer tok-123"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode(), headers=headers,
        )
        return json.loads(urllib.request.urlopen(req, timeout=5).read())

    def test_batch_endpoint(self):
        srv, received = self._server()
        try:
            resp = self._post("/download_many", {
                "urls": ["https://a.com/1.zip", "https://b.com/2.zip",
                         "https://c.com/3.zip", "not-a-url", "file:///etc/passwd"],
            })
            self.assertEqual(resp["accepted"], 3)
            self.assertEqual(resp["rejected"], 2)
            deadline = time.time() + 5
            while time.time() < deadline and len(received) < 3:
                time.sleep(0.05)
            self.assertEqual(received,
                             ["https://a.com/1.zip", "https://b.com/2.zip", "https://c.com/3.zip"])
        finally:
            srv.stop()

    def test_batch_deduplicates(self):
        srv, received = self._server()
        try:
            self._post("/download_many", {"urls": ["https://a.com/1.zip",
                                                   "https://a.com/1.zip", ""]})
            deadline = time.time() + 5
            while time.time() < deadline and len(received) < 1:
                time.sleep(0.05)
            self.assertEqual(received, ["https://a.com/1.zip"])
        finally:
            srv.stop()

    def test_single_endpoint_and_auth(self):
        srv, _received = self._server()
        try:
            resp = self._post("/download", {"url": "https://d.com/4.zip"})
            self.assertEqual(resp["status"], "queued")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._post("/download_many", {"urls": []}, token=False)
            self.assertEqual(ctx.exception.code, 401)
        finally:
            srv.stop()


if __name__ == "__main__":
    unittest.main()
