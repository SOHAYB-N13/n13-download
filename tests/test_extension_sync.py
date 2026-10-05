"""Regression tests for the extension token-sync mechanism.

The browser extension authenticates with the Live Server using the token in
``chrome_extension/token.json``.  That file is a snapshot written by
``create_chrome_extension``; if it drifts from the running N13 config the
extension reports "authorization failed".  ``sync_extension_token`` is the
reliable recovery: it mirrors the existing credential (never rotates it).
"""

from __future__ import annotations

import json
import sys
import unittest
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from browser.protocol import sync_extension_token, token_file_payload


class TokenSyncTest(unittest.TestCase):
    def test_token_file_payload_carries_real_credential(self):
        cfg = AppConfig()
        cfg.live_server_port = 6868
        cfg.live_server_token = "abc-123-token"
        payload = json.loads(token_file_payload(cfg))
        self.assertEqual(payload["token"], "abc-123-token")
        self.assertEqual(payload["live_server_url"], "http://127.0.0.1:6868/download")

    def test_sync_writes_matching_token_and_never_rotates(self):
        cfg = AppConfig()
        cfg.live_server_token = "stable-token"
        with tempfile.TemporaryDirectory() as tmp:
            ext_dir = Path(tmp) / "chrome_extension"
            ext_dir.mkdir()
            written = sync_extension_token(cfg, ext_dir=ext_dir)
            self.assertIsNotNone(written)
            data = json.loads((ext_dir / "token.json").read_text(encoding="utf-8"))
            # The sync mirrors the existing token exactly — it never invents one.
            self.assertEqual(data["token"], "stable-token")

    def test_sync_generates_extension_when_missing(self):
        cfg = AppConfig()
        cfg.live_server_token = "auto-token"
        with tempfile.TemporaryDirectory() as tmp:
            ext_dir = Path(tmp) / "chrome_extension"
            written = sync_extension_token(cfg, ext_dir=ext_dir)
            # A fresh install (no chrome_extension/) must end up with a loadable,
            # correctly-authenticated extension — never None.
            self.assertIsNotNone(written)
            self.assertTrue(ext_dir.is_dir())
            self.assertTrue((ext_dir / "manifest.json").exists())
            self.assertTrue((ext_dir / "background.js").exists())
            data = json.loads((ext_dir / "token.json").read_text(encoding="utf-8"))
            self.assertEqual(data["token"], "auto-token")

    def test_ensure_extension_icons_noop_when_assets_missing(self):
        # In a frozen (installed) build, assets/icons is not bundled; the
        # extension template already ships every icon, so this must not raise.
        from unittest.mock import patch
        from browser.icons import ensure_extension_icons
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ext = root / "ext"
            ext.mkdir()
            with patch("browser.icons._project_root", return_value=root):
                ensure_extension_icons(ext)  # must not raise

    def test_ensure_extension_icons_copies_when_assets_present(self):
        from unittest.mock import patch
        import shutil as _shutil
        from browser.icons import ensure_extension_icons
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            icons = root / "assets" / "icons"
            icons.mkdir(parents=True)
            (icons / "icon-16.png").write_bytes(b"png")
            ext = root / "ext"
            ext.mkdir()
            with patch("browser.icons._project_root", return_value=root):
                ensure_extension_icons(ext)
            self.assertEqual((ext / "icon16.png").read_bytes(), b"png")


if __name__ == "__main__":
    unittest.main()
