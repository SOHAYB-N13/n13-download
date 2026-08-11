"""Release hardening — user-data paths and legacy migration."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.paths as paths
from config.loader import load_config


class PathsTest(unittest.TestCase):
    def tearDown(self):
        # Restore real path functions.
        import core.paths as P
        P.user_data_dir = P._real_user_data_dir
        P._LEGACY_CONFIG_DIR = P._real_legacy_config_dir

    def setUp(self):
        import core.paths as P
        if not hasattr(P, "_real_user_data_dir"):
            P._real_user_data_dir = P.user_data_dir
            P._real_legacy_config_dir = P._LEGACY_CONFIG_DIR
        self.tmp = Path(tempfile.mkdtemp())
        P.user_data_dir = lambda: self.tmp
        P._LEGACY_CONFIG_DIR = self.tmp / "legacy-config"

    def test_user_data_dir_under_localappdata(self):
        # With default (unpatched) behaviour on Windows, data lives under LOCALAPPDATA.
        if sys.platform == "win32":
            la = os.environ.get("LOCALAPPDATA")
            self.assertTrue(la, "LOCALAPPDATA missing on Windows")
            self.assertEqual(paths._real_user_data_dir(),
                             Path(la) / "N13")

    def test_migrate_legacy_config(self):
        legacy = self.tmp / "legacy-config"
        legacy.mkdir(parents=True)
        (legacy / "config.json").write_text('{"num_threads": 9}', encoding="utf-8")
        (legacy / "ui_prefs.json").write_text('{"theme":"dark"}', encoding="utf-8")

        paths.migrate_legacy_config()

        new_cfg = paths.config_dir() / "config.json"
        self.assertTrue(new_cfg.exists(), "config not migrated")
        self.assertEqual(json.loads(new_cfg.read_text())["num_threads"], 9)
        self.assertTrue((paths.config_dir() / "ui_prefs.json").exists())
        # Idempotent: a second run must not fail or duplicate.
        paths.migrate_legacy_config()
        self.assertEqual(json.loads(new_cfg.read_text())["num_threads"], 9)

    def test_migrate_legacy_saved_links(self):
        project_root = self.tmp / "proj"
        old = project_root / "saved_links"
        old.mkdir(parents=True)
        (old / "downloads.db").write_bytes(b"sqlite-header" + b"\x00" * 100)
        (old / "gui_queue.json").write_text("[]", encoding="utf-8")
        (old / "links_20260101.json").write_text('{"urls": []}', encoding="utf-8")
        (old / "batch_resume.json").write_text("{}", encoding="utf-8")

        paths.migrate_legacy_saved_links(project_root)

        self.assertTrue((paths.data_dir() / "downloads.db").exists(), "db not migrated")
        self.assertTrue((paths.data_dir() / "gui_queue.json").exists())
        self.assertTrue((paths.saved_links_dir() / "links_20260101.json").exists())
        self.assertTrue((paths.saved_links_dir() / "batch_resume.json").exists())
        # Originals preserved.
        self.assertTrue((old / "downloads.db").exists())
        # Idempotent.
        paths.migrate_legacy_saved_links(project_root)
        self.assertEqual(
            list((paths.data_dir() / "downloads.db").read_bytes()),
            list((old / "downloads.db").read_bytes()),
        )

    def test_load_config_reads_migrated_location(self):
        legacy = self.tmp / "legacy-config"
        legacy.mkdir(parents=True)
        (legacy / "config.json").write_text('{"num_threads": 7}', encoding="utf-8")
        paths.migrate_legacy_config()
        cfg = load_config(paths.config_dir() / "config.json")
        self.assertEqual(cfg.num_threads, 7)


if __name__ == "__main__":
    unittest.main()
