"""System-stats resilience (``Api.get_system_stats``).

The dashboard polls this every two seconds.  When the download folder is not
there — deleted, renamed, on an unplugged USB disk, on an offline network
share — ``psutil.disk_usage`` raised ``FileNotFoundError``, which escaped the
method.  pywebview rejected the JS promise, and because the caller awaits
``Promise.all([getStats, getSystemStats])`` the *whole* dashboard refresh died:
download counters froze too, and a traceback was written to the error log every
two seconds for as long as the folder stayed missing.

These tests pin the contract: the method always returns a dict, always reports
CPU/RAM, and says "unknown" for disk rather than inventing zero.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.session import SessionManager
from ui.api import Api, _disk_usage_anchor


def _api_with_dir(directory: str) -> Api:
    cfg = AppConfig()
    cfg.download_dir = directory
    return Api(cfg, SessionManager(cfg))


class DiskUsageAnchorTest(unittest.TestCase):
    def test_existing_directory_is_used_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_disk_usage_anchor(tmp), str(Path(tmp)))

    def test_missing_child_falls_back_to_existing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "gone" / "deeper")
            self.assertEqual(_disk_usage_anchor(missing), str(Path(tmp)))

    def test_relative_and_empty_paths_never_raise(self):
        for value in ("", ".", "relative/path/that/does/not/exist"):
            self.assertIsInstance(_disk_usage_anchor(value), str)


class GetSystemStatsTest(unittest.TestCase):
    def test_missing_download_dir_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = _api_with_dir(str(Path(tmp) / "deleted-folder"))
            stats = api.get_system_stats()
            self.assertIsInstance(stats, dict)
            # Disk is still reported, from the volume that exists.
            self.assertGreater(stats["disk_total"], 0)
            self.assertGreaterEqual(stats["disk_free"], 0)

    def test_unreachable_volume_reports_unknown_not_zero_bytes(self):
        # No volume could be identified (the anchor found nothing), so the
        # payload must say "unknown" rather than claiming 0 B free.
        api = _api_with_dir("Q:\\n13-does-not-exist\\downloads")
        with mock.patch("ui.api._disk_usage_anchor", return_value=""):
            stats = api.get_system_stats()
        self.assertEqual(stats["disk_total"], 0)
        self.assertFalse(stats["disk_known"])

    def test_disk_query_failure_does_not_escape(self):
        # The volume can disappear between the anchor check and the query (a
        # USB disk unplugged mid-poll).  psutil then raises OSError.
        import psutil

        api = _api_with_dir(tempfile.gettempdir())
        with mock.patch.object(psutil, "disk_usage", side_effect=OSError("gone")):
            stats = api.get_system_stats()
        self.assertIsInstance(stats, dict)
        self.assertEqual(stats["disk_total"], 0)
        self.assertFalse(stats["disk_known"])

    def test_always_reports_cpu_and_ram(self):
        api = _api_with_dir(tempfile.gettempdir())
        with mock.patch("ui.api._disk_usage_anchor", return_value=""):
            stats = api.get_system_stats()
        self.assertIn("cpu_percent", stats)
        self.assertIn("ram_percent", stats)
        self.assertGreaterEqual(stats["cpu_percent"], 0)

    def test_payload_keys_are_stable(self):
        api = _api_with_dir(tempfile.gettempdir())
        stats = api.get_system_stats()
        for key in (
            "cpu_percent", "ram_percent", "disk_free", "disk_free_display",
            "disk_total", "disk_total_display", "disk_used", "disk_used_display",
            "disk_percent", "disk_known", "session_downloaded",
            "session_downloaded_display",
        ):
            self.assertIn(key, stats, msg=key)


if __name__ == "__main__":
    unittest.main()
