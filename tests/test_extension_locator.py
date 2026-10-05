"""Unit tests for Stage 1: dynamic N13 extension directory discovery.

Covers source and frozen (PyInstaller) layouts, manifest validation, and the
materialize-from-template fallback.  Nothing here requires Chrome.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from browser.extension_locator import (
    ExtensionLocatorError,
    app_root,
    candidate_dirs,
    discover_extension_dir,
    materialize_extension_dir,
    validate_extension_dir,
)


def _make_extension(root: Path, name: str = "N13 Download Manager") -> Path:
    """Create a minimal-but-valid N13 extension tree."""
    ext = root / "chrome_extension"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / "manifest.json").write_text(json.dumps({
        "manifest_version": 3,
        "name": name,
        "version": "1.0.0",
        "background": {"service_worker": "background.js"},
        "action": {"default_popup": "popup.html", "default_icon": {"16": "icon16.png"}},
        "icons": {"16": "icon16.png", "128": "icon128.png"},
        "content_scripts": [{"matches": ["http://*/*"], "js": ["content.js"]}],
    }), encoding="utf-8")
    for f in ("background.js", "popup.html", "icon16.png", "icon128.png", "content.js"):
        (ext / f).write_bytes(b"x")
    return ext


class AppRootTest(unittest.TestCase):
    def test_source_root_is_project_root(self):
        self.assertEqual(app_root(), Path(__file__).resolve().parent.parent)

    def test_frozen_root_uses_meipass(self):
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "_MEIPASS", "C:\\Program Files\\N13 Download Manager\\_internal", create=True):
            self.assertEqual(app_root(), Path("C:\\Program Files\\N13 Download Manager\\_internal"))

    def test_frozen_root_falls_back_to_executable_dir(self):
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "_MEIPASS", None, create=True), \
             patch.object(sys, "executable", "C:\\N13\\N13.exe"):
            self.assertEqual(app_root(), Path("C:\\N13"))


class ValidateTest(unittest.TestCase):
    def test_rejects_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, reason = validate_extension_dir(Path(tmp) / "nope")
            self.assertFalse(ok)
            self.assertIn("not a directory", reason)

    def test_rejects_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "chrome_extension"
            d.mkdir()
            ok, reason = validate_extension_dir(d)
            self.assertFalse(ok)
            self.assertIn("manifest.json missing", reason)

    def test_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "chrome_extension"
            d.mkdir()
            (d / "manifest.json").write_text("{not json", encoding="utf-8")
            ok, reason = validate_extension_dir(d)
            self.assertFalse(ok)
            self.assertIn("not valid JSON", reason)

    def test_rejects_unrelated_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "chrome_extension"
            d.mkdir()
            (d / "manifest.json").write_text(json.dumps({"name": "Random Tool"}), encoding="utf-8")
            ok, reason = validate_extension_dir(d)
            self.assertFalse(ok)
            self.assertIn("not N13", reason)

    def test_rejects_missing_referenced_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "chrome_extension"
            d.mkdir()
            (d / "manifest.json").write_text(json.dumps({
                "name": "N13 Download Manager",
                "background": {"service_worker": "background.js"},
            }), encoding="utf-8")
            ok, reason = validate_extension_dir(d)
            self.assertFalse(ok)
            self.assertIn("background.js", reason)

    def test_accepts_complete_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _make_extension(Path(tmp))
            ok, reason = validate_extension_dir(d)
            self.assertTrue(ok, reason)


class DiscoveryTest(unittest.TestCase):
    def test_discovers_own_chrome_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            ext = _make_extension(Path(tmp))
            with patch("browser.extension_locator.app_root", return_value=Path(tmp)):
                found = discover_extension_dir()
            self.assertEqual(found.resolve(), ext.resolve())

    def test_prefers_chrome_extension_over_foreign_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = _make_extension(root)
            # A decoy "chrome_extension" elsewhere on disk must never win.
            decoy = Path(tmp) / "other" / "chrome_extension"
            decoy.mkdir(parents=True)
            (decoy / "manifest.json").write_text(json.dumps({"name": "Something Else"}), encoding="utf-8")
            with patch("browser.extension_locator.app_root", return_value=root):
                found = discover_extension_dir()
            self.assertEqual(found.resolve(), good.resolve())

    def test_materializes_from_template_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_extension(root)  # creates chrome_extension...
            (root / "chrome_extension").rename(root / "extension")  # ...turn it into the template
            with patch("browser.extension_locator.app_root", return_value=root):
                found = discover_extension_dir()
            self.assertTrue((found / "manifest.json").is_file())
            ok, _ = validate_extension_dir(found)
            self.assertTrue(ok)

    def test_frozen_layout_under_program_files_uses_user_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_extension(root)
            (root / "chrome_extension").rename(root / "extension")
            user = Path(tmp) / "user"
            # Simulate read-only _internal: only the template is present.
            with patch("browser.extension_locator.app_root", return_value=root), \
                 patch("browser.extension_locator.user_extension_dir", return_value=user), \
                 patch("browser.extension_locator._writable",
                       side_effect=lambda d: str(d).startswith(str(user))):
                found = discover_extension_dir()
            # Must fall back to the per-user writable copy.
            self.assertTrue(str(found).startswith(str(user)))
            ok, _ = validate_extension_dir(found)
            self.assertTrue(ok)

    def test_raises_when_nothing_found_or_creatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("browser.extension_locator.app_root", return_value=root), \
                 patch("browser.extension_locator.user_extension_dir", return_value=Path(tmp) / "user"), \
                 patch("browser.extension_locator._writable", return_value=False):
                with self.assertRaises(ExtensionLocatorError):
                    discover_extension_dir()

    def test_candidate_dirs_are_app_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("browser.extension_locator.app_root", return_value=root), \
                 patch("browser.extension_locator.user_extension_dir", return_value=Path(tmp) / "user"):
                dirs = candidate_dirs()
            self.assertEqual(dirs[0], root / "chrome_extension")
            self.assertEqual(dirs[1], Path(tmp) / "user")

    def test_template_is_never_a_direct_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_extension(root)
            (root / "chrome_extension").rename(root / "extension")  # template only
            with patch("browser.extension_locator.app_root", return_value=root), \
                 patch("browser.extension_locator.user_extension_dir", return_value=Path(tmp) / "user"):
                dirs = candidate_dirs()
            # The template must NOT be chosen directly; it is materialized.
            self.assertNotIn(root / "extension", dirs)


if __name__ == "__main__":
    unittest.main()