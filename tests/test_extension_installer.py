"""Unit tests for the Chrome automation helpers that do not need Chrome.

Covers the Preferences-file installation verification (Stage 9) and basic
ControlRef identity logic.  The live Chrome UI workflow is tested by the
real-Windows stage runner (python -m browser.extension_installer N).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from browser.chrome_automation import extension_in_preferences


def _write_preferences(root: Path, profile: str, entries: dict) -> None:
    profile_dir = root / profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    pref = profile_dir / "Preferences"
    pref.write_text(json.dumps({"extensions": {"settings": entries}}), encoding="utf-8")


class PreferencesScanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.ext_dir = Path("C:/Apps/N13 Download Manager/_internal/chrome_extension")
        patcher = patch.dict(os.environ, {
            "LOCALAPPDATA": str(self.root / "AppData" / "Local"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        chrome_data = self.root / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
        chrome_data.mkdir(parents=True)

    def test_finds_extension_by_exact_path(self):
        _write_preferences(self.root / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
                           "Default", {
            "abc123": {
                "path": "C:\\Apps\\N13 Download Manager\\_internal\\chrome_extension",
                "state": None,
                "manifest": {"name": "N13 Download Manager"},
            },
        })
        record = extension_in_preferences(self.ext_dir, "N13 Download Manager")
        self.assertIsNotNone(record)
        self.assertEqual(record["extension_id"], "abc123")

    def test_finds_extension_by_manifest_name_fallback(self):
        # Path differs (e.g. a per-user copy) — name matching still finds it.
        _write_preferences(self.root / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
                           "Default", {
            "xyz789": {
                "path": "C:\\Users\\me\\AppData\\Local\\N13\\chrome_extension",
                "state": 1,
                "manifest": {"name": "N13 Download Manager"},
            },
        })
        record = extension_in_preferences(self.ext_dir, "N13 Download Manager")
        self.assertIsNotNone(record)
        self.assertEqual(record["extension_id"], "xyz789")
        self.assertEqual(record["state"], 1)

    def test_ignores_unrelated_extensions(self):
        _write_preferences(self.root / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
                           "Default", {
            "other1": {
                "path": "D:\\random\\folder",
                "manifest": {"name": "Random Extension"},
            },
        })
        self.assertIsNone(extension_in_preferences(self.ext_dir, "N13 Download Manager"))

    def test_correct_profile_scan(self):
        base = self.root / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
        _write_preferences(base, "Default", {
            "aaa": {"path": "C:\\Apps\\N13 Download Manager\\_internal\\chrome_extension",
                    "manifest": {"name": "N13 Download Manager"}},
        })
        _write_preferences(base, "Profile 2", {
            "bbb": {"path": "C:\\elsewhere", "manifest": {"name": "Other"}},
        })
        record = extension_in_preferences(self.ext_dir, "N13 Download Manager")
        self.assertEqual(record["extension_id"], "aaa")


class ControlRefIdentityTest(unittest.TestCase):
    def test_identity_key_matches(self):
        from unittest.mock import MagicMock

        from browser.windows_ui import ControlRef

        a = MagicMock()
        a.Name = "Select Folder"
        a.ControlTypeName = "ButtonControl"
        a.AutomationId = "1"
        a.ClassName = "Button"
        b = MagicMock()
        b.Name = "select folder"
        b.ControlTypeName = "ButtonControl"
        b.AutomationId = "1"
        b.ClassName = "Button"
        ra, rb = ControlRef(a), ControlRef(b)
        self.assertEqual(ra.identity_key(), rb.identity_key())

    def test_identity_key_differs(self):
        from unittest.mock import MagicMock

        from browser.windows_ui import ControlRef

        a = MagicMock()
        a.Name = "Cancel"
        a.ControlTypeName = "ButtonControl"
        a.AutomationId = "2"
        a.ClassName = "Button"
        b = MagicMock()
        b.Name = "Select Folder"
        b.ControlTypeName = "ButtonControl"
        b.AutomationId = "1"
        b.ClassName = "Button"
        self.assertNotEqual(ControlRef(a).identity_key(), ControlRef(b).identity_key())


if __name__ == "__main__":
    unittest.main()