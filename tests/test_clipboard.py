"""Phase I — clipboard monitor URL detection (pure logic, no tkinter needed)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.clipboard import ClipboardMonitor


class ClipboardUrlTest(unittest.TestCase):
    def test_single_url(self):
        self.assertEqual(
            ClipboardMonitor._looks_like_url("https://example.com/a.zip"),
            "https://example.com/a.zip",
        )
        self.assertEqual(
            ClipboardMonitor._looks_like_url("http://x.test/b"),
            "http://x.test/b",
        )

    def test_multiline_text_with_url(self):
        text = "Some copied text\nhttps://example.com/file.mp4\nmore text"
        self.assertEqual(
            ClipboardMonitor._looks_like_url(text),
            "https://example.com/file.mp4",
        )

    def test_no_url(self):
        self.assertIsNone(ClipboardMonitor._looks_like_url("just some text"))
        self.assertIsNone(ClipboardMonitor._looks_like_url(""))
        self.assertIsNone(ClipboardMonitor._looks_like_url("ftp://example.com/a"))

    def test_url_inside_line_is_ignored(self):
        # Only a line that *starts* with http(s) counts (a bare word URL mid
        # sentence is usually prose, not a copy-paste download link).
        self.assertIsNone(
            ClipboardMonitor._looks_like_url("visit https://example.com/a for info")
        )


if __name__ == "__main__":
    unittest.main()
