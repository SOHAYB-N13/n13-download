"""Phase 3 — Download Rules & Automation (focused tests)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.rules import DownloadRule, RuleCondition, RuleEngine
from core.session import SessionManager
from ui.api import Api


def _rule(**kw) -> DownloadRule:
    r = DownloadRule(**kw)
    return r


class RuleEngineTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "rules.json"
        self.engine = RuleEngine(self.path)

    def test_extension_match(self):
        r = _rule(name="videos", conditions=[RuleCondition("extension", "mp4")],
                  category="Videos", folder="D:/V")
        self.assertTrue(r.matches("https://x/movie.mp4", "movie.mp4", 0, ""))
        self.assertFalse(r.matches("https://x/song.mp3", "song.mp3", 0, ""))

    def test_domain_match(self):
        r = _rule(name="example", conditions=[RuleCondition("domain", "example.com")],
                  folder="D:/Example")
        self.assertTrue(r.matches("https://example.com/a.bin", "a.bin", 0, ""))
        self.assertTrue(r.matches("https://sub.example.com/a.bin", "a.bin", 0, ""))
        self.assertFalse(r.matches("https://other.com/a.bin", "a.bin", 0, ""))

    def test_and_conditions(self):
        r = _rule(conditions=[RuleCondition("extension", "mp4"),
                             RuleCondition("domain", "example.com")])
        self.assertTrue(r.matches("https://example.com/m.mp4", "m.mp4", 0, ""))
        self.assertFalse(r.matches("https://other.com/m.mp4", "m.mp4", 0, ""))

    def test_size_conditions(self):
        r = _rule(conditions=[RuleCondition("min_size", "1000"),
                             RuleCondition("max_size", "5000")])
        self.assertTrue(r.matches("https://x/a.bin", "a.bin", 2000, ""))
        self.assertFalse(r.matches("https://x/a.bin", "a.bin", 9999, ""))
        self.assertFalse(r.matches("https://x/a.bin", "a.bin", 100, ""))

    def test_priority_and_tiebreak(self):
        e = self.engine
        e.add(_rule(name="low", priority=1, conditions=[RuleCondition("extension", "bin")],
                    category="Low"))
        e.add(_rule(name="high", priority=5, conditions=[RuleCondition("extension", "bin")],
                    category="High"))
        m = e.evaluate("https://x/a.bin", "a.bin")
        self.assertEqual(m.name, "high")
        # Disable high → low wins.
        e.update(m.id, {"enabled": False})
        m2 = e.evaluate("https://x/a.bin", "a.bin")
        self.assertEqual(m2.name, "low")

    def test_persistence_roundtrip(self):
        e = RuleEngine(self.path)
        e.add(_rule(name="persisted", conditions=[RuleCondition("extension", "pdf")],
                    folder="D:/PDF", priority_value=3, connection_mode="manual",
                    manual_connections=6))
        e2 = RuleEngine(self.path)
        self.assertEqual(len(e2.all()), 1)
        r = e2.all()[0]
        self.assertEqual(r["name"], "persisted")
        self.assertEqual(r["conditions"][0]["field"], "extension")
        self.assertEqual(r["connection_mode"], "manual")

    def test_crud(self):
        e = self.engine
        rid = e.add(_rule(name="x"))
        self.assertTrue(e.duplicate(rid))
        self.assertEqual(len(e.all()), 2)
        self.assertTrue(e.update(rid, {"name": "y", "priority": 7}))
        self.assertEqual(e.all()[0]["name"], "y")
        self.assertTrue(e.delete(rid))
        self.assertEqual(len(e.all()), 1)


class ApiRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AppConfig()
        self.cfg.block_private_urls = False
        self.cfg.connection_mode = "manual"
        self.cfg.rules_enabled = True
        # Isolate the Api's task store from the real user data directory:
        # tasks added by these tests (autostart=False) must never persist into
        # %LOCALAPPDATA%/N13 and resurface as stale duplicates in later runs.
        from unittest.mock import patch

        import core.paths

        self._data_dir_patch = patch.object(
            core.paths, "data_dir", return_value=self.tmp / "data"
        )
        self._data_dir_patch.start()
        self.addCleanup(self._data_dir_patch.stop)
        self.api = Api(self.cfg, SessionManager(self.cfg))
        self.api._rules._path = self.tmp / "rules.json"   # isolated storage

    def tearDown(self):
        self.api.shutdown()

    def test_rule_applies_category_folder_and_priority(self):
        rid = self.api.add_rule({
            "name": "vids", "priority": 5,
            "conditions": [{"field": "extension", "value": "mp4"}],
            "category": "Videos", "folder": "D:/R/Videos",
            "priority_value": 2, "connection_mode": "manual", "manual_connections": 6,
        })
        self.assertTrue(rid)
        tid = self.api.add_download("https://x.example/movie.mp4", directory="",
                                    label="movie.mp4", autostart=False)
        snap = self.api.get_download(tid)
        self.assertEqual(snap["category"], "Videos")
        self.assertEqual(snap["directory"], "D:/R/Videos")
        self.assertEqual(snap["priority"], 2)
        self.assertEqual(snap["connection_mode"], "manual")

    def test_user_override_beats_rule(self):
        self.api.add_rule({
            "name": "vids", "priority": 5,
            "conditions": [{"field": "extension", "value": "mp4"}],
            "category": "Videos", "folder": "D:/R/Videos",
        })
        tid = self.api.add_download("https://x.example/movie.mp4",
                                    directory="C:/UserChoice", label="movie.mp4",
                                    autostart=False)
        self.assertEqual(self.api.get_download(tid)["directory"], "C:/UserChoice")

    def test_test_rule(self):
        self.api.add_rule({
            "name": "docs", "priority": 1,
            "conditions": [{"field": "extension", "value": "pdf"}],
            "category": "Documents",
        })
        res = self.api.test_rule("https://x.example/report.pdf")
        self.assertTrue(res["matched"])
        self.assertEqual(res["rule"]["name"], "docs")
        self.assertEqual(res["actions"]["category"], "Documents")
        res2 = self.api.test_rule("https://x.example/movie.mp4")
        self.assertFalse(res2["matched"])

    def test_rules_disabled(self):
        self.cfg.rules_enabled = False
        self.api.add_rule({
            "name": "vids", "priority": 5,
            "conditions": [{"field": "extension", "value": "mp4"}],
            "folder": "D:/R/Videos",
        })
        tid = self.api.add_download("https://x.example/movie.mp4", directory="",
                                    label="movie.mp4", autostart=False)
        # The rule's FOLDER must not be applied when rules are disabled
        # (auto-categorize still detects Videos, so check the folder).
        self.assertNotEqual(self.api.get_download(tid)["directory"], "D:/R/Videos")


if __name__ == "__main__":
    unittest.main()
