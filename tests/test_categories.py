"""Phase H — categories + configurable detection + directory routing."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppConfig
from core.analyzer import detect_category
from ui.common import DownloadRequest, TaskManager


class CategoryDetectionTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(detect_category("movie.mp4"), "Videos")
        self.assertEqual(detect_category("song.flac"), "Music")
        self.assertEqual(detect_category("photo.jpg"), "Images")
        self.assertEqual(detect_category("doc.pdf"), "Documents")
        self.assertEqual(detect_category("archive.zip"), "Archives")
        self.assertEqual(detect_category("setup.exe"), "Programs")
        self.assertEqual(detect_category("weird.bin"), "Other")
        self.assertEqual(detect_category("noextension"), "General")

    def test_content_type_fallback(self):
        self.assertEqual(detect_category("file", "video/mp4"), "Videos")
        self.assertEqual(detect_category("file", "audio/ogg"), "Music")
        self.assertEqual(detect_category("file", "application/zip"), "Archives")
        self.assertEqual(detect_category("file", "application/unknown-thing"), "Other")

    def test_custom_extension_map(self):
        custom = {"Videos": ["customv"], "Music": ["myext"]}
        self.assertEqual(detect_category("x.customv", ext_map=custom), "Videos")
        self.assertEqual(detect_category("x.myext", ext_map=custom), "Music")
        # Defaults still apply for unmapped extensions.
        self.assertEqual(detect_category("movie.mp4", ext_map=custom), "Videos")
        self.assertEqual(detect_category("doc.pdf", ext_map=custom), "Documents")


class DirectoryRoutingTest(unittest.TestCase):
    def test_resolve_category_dir(self):
        cfg = AppConfig()
        cfg.download_dir = "C:/Downloads"
        base = cfg.download_dir
        # Automatic category routing: <base>/<category>.
        self.assertEqual(cfg.resolve_category_dir("Videos", base),
                         os.path.join(base, "Videos"))
        self.assertEqual(cfg.resolve_category_dir("Music", base),
                         os.path.join(base, "Music"))
        self.assertEqual(cfg.resolve_category_dir("Archives", base),
                         os.path.join(base, "Archives"))
        # Explicit per-category overrides win over automatic routing.
        cfg.category_dirs = {"Videos": "D:/Media/Videos", "Music": "D:/Media/Music"}
        self.assertEqual(cfg.resolve_category_dir("Videos", base),
                         "D:/Media/Videos")
        self.assertEqual(cfg.resolve_category_dir("Music", base),
                         "D:/Media/Music")
        # General / empty fall back to base.
        self.assertEqual(cfg.resolve_category_dir("General", base), base)
        self.assertEqual(cfg.resolve_category_dir("", base), base)

    def test_resolve_category_dir_rejects_unsafe_names(self):
        cfg = AppConfig()
        base = cfg.download_dir = "C:/Downloads"
        # Traversal / separators / Windows-invalid characters never escape base.
        for bad in ("../evil", "a/b", "a\\b", "a:b", "a*b", "..", ".", ""):
            self.assertEqual(cfg.resolve_category_dir(bad, base), base, msg=bad)
        # Custom safe names still route.
        self.assertEqual(cfg.resolve_category_dir("Compressed", base),
                         os.path.join(base, "Compressed"))

    def test_serialization_preserves_dict(self):
        cfg = AppConfig()
        cfg.category_dirs = {"Videos": "D:/V"}
        cfg.category_extensions = {"Videos": ["mp4", "mkv"]}
        d = cfg.to_dict()
        cfg2 = AppConfig.from_dict(d)
        self.assertEqual(cfg2.category_dirs, {"Videos": "D:/V"})
        self.assertEqual(cfg2.category_extensions, {"Videos": ["mp4", "mkv"]})


class QueueCategoryTest(unittest.TestCase):
    def test_auto_categorize_on_add(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = AppConfig()
        cfg.download_dir = str(tmp)
        cfg.auto_categorize = True
        from ui.legacy import LegacyDownloadRunner
        from core.session import SessionManager
        m = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                        tmp, max_concurrent=1, config=cfg)
        tid = m.add(DownloadRequest(url="https://x/movie.mp4", directory=str(tmp)),
                    autostart=False)
        self.assertEqual(m.get(tid).category, "Videos")
        tid2 = m.add(DownloadRequest(url="https://x/app.exe", directory=str(tmp)),
                     autostart=False)
        self.assertEqual(m.get(tid2).category, "Programs")
        # Explicit category wins.
        tid3 = m.add(DownloadRequest(url="https://x/thing.exe", directory=str(tmp),
                                     category="Documents"), autostart=False)
        self.assertEqual(m.get(tid3).category, "Documents")
        m.close()

    def test_auto_categorize_disabled(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = AppConfig()
        cfg.download_dir = str(tmp)
        cfg.auto_categorize = False
        from ui.legacy import LegacyDownloadRunner
        from core.session import SessionManager
        m = TaskManager(LegacyDownloadRunner(cfg, SessionManager(cfg), log=lambda *a, **k: None),
                        tmp, max_concurrent=1, config=cfg)
        tid = m.add(DownloadRequest(url="https://x/movie.mp4", directory=str(tmp)),
                    autostart=False)
        self.assertEqual(m.get(tid).category, "General")
        m.close()


if __name__ == "__main__":
    unittest.main()
