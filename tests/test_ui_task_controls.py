"""UI task controls — rename and per-download speed limit.

Covers the two backend capabilities the redesigned UI depends on:

* ``TaskManager.rename_task`` — must refuse active tasks, must never escape the
  task directory, must keep the on-disk file in sync for completed tasks, and
  must not clobber an existing destination.
* ``TaskManager.set_task_speed_limit`` — must persist on the task and be
  surfaced through the snapshot consumed by the frontend.

Uses a fake runner (no network) for deterministic control.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.clipboard import extract_url, read_clipboard_text
from core.task import TaskStatus
from ui.common import DownloadRequest, TaskManager, _sanitize_filename

SIZE = 100


class FakeRunner:
    """Deterministic runner: analyze OK, download completes immediately."""

    def __init__(self) -> None:
        self.last_error = ""

    def analyze(self, task_id, request, control):
        return SimpleNamespace(
            ok=True,
            total_size=SIZE,
            supports_range=True,
            filename="orig.zip",
            content_type="application/zip",
            server="test",
            etag="",
            last_modified="",
        )

    def download(
        self,
        task_id,
        request,
        analysis,
        progress,
        control,
        status_callback=None,
        path_callback=None,
        smart_callback=None,
    ):
        if status_callback:
            status_callback("DOWNLOADING")
        progress(SIZE, SIZE)
        return True


class SanitizeFilenameTests(unittest.TestCase):
    def test_keeps_plain_names(self):
        self.assertEqual(_sanitize_filename("movie.mkv"), "movie.mkv")
        self.assertEqual(_sanitize_filename("  spaced.txt  "), "spaced.txt")

    def test_strips_directory_traversal(self):
        # Only the final component survives, so a rename can never escape.
        self.assertEqual(_sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(_sanitize_filename(r"..\..\win.ini"), "win.ini")
        self.assertEqual(_sanitize_filename(r"C:\Users\x\a.zip"), "a.zip")

    def test_rejects_unusable_names(self):
        for bad in ("", "   ", ".", "..", "...", "<>:?"):
            self.assertEqual(_sanitize_filename(bad), "", msg=repr(bad))

    def test_replaces_reserved_characters(self):
        cleaned = _sanitize_filename('bad<>:name?.mp4')
        self.assertTrue(cleaned.endswith(".mp4"))
        self.assertNotIn("<", cleaned)
        self.assertNotIn(":", cleaned)
        self.assertNotIn("?", cleaned)


class ClipboardHelperTests(unittest.TestCase):
    """The "Paste URL" action depends on these two helpers."""

    def test_extract_url_finds_first_http_link(self):
        self.assertEqual(extract_url("https://a.com/f.zip"), "https://a.com/f.zip")
        self.assertEqual(extract_url("http://a.com/f.zip"), "http://a.com/f.zip")

    def test_extract_url_scans_multiline_paste(self):
        text = "hello\n  https://a.com/f.zip  \ntrailing"
        self.assertEqual(extract_url(text), "https://a.com/f.zip")

    def test_extract_url_ignores_non_links(self):
        for bad in (None, "", "   ", "just some text", "ftp://a.com/f.zip"):
            self.assertIsNone(extract_url(bad), msg=repr(bad))

    def test_read_clipboard_text_is_bounded_and_never_raises(self):
        # Must always return a str and must not block on a hung clipboard owner.
        value = read_clipboard_text(timeout=0.5)
        self.assertIsInstance(value, str)


class TaskControlTests(unittest.TestCase):
    def setUp(self) -> None:
        # mkdtemp (not TemporaryDirectory): the SQLite store keeps the db file
        # open, which makes auto-rmtree fail on Windows.  close() in tearDown
        # releases the handle first.
        self.dir = tempfile.mkdtemp()
        self.manager = TaskManager(FakeRunner(), Path(self.dir), max_concurrent=3)
        # autostart=False keeps the task deterministically QUEUED — otherwise
        # the worker can race ahead and complete it before the test runs.
        self.task_id = self.manager.add(
            DownloadRequest(
                url="https://example.com/orig.zip",
                directory=self.dir,
                label="orig.zip",
            ),
            autostart=False,
        )

    def tearDown(self) -> None:
        try:
            self.manager.close()
        except Exception:
            pass

    # ── per-download speed limit ─────────────────────────────────────

    def test_speed_limit_persists_and_is_exposed(self):
        self.manager.set_task_speed_limit(self.task_id, 512 * 1024)
        snap = self.manager.get(self.task_id)
        self.assertEqual(snap.request.speed_limit_bps, 512 * 1024)
        self.assertEqual(snap.to_dict()["speed_limit_bps"], 512 * 1024)

    def test_speed_limit_clamps_negative_to_unlimited(self):
        self.manager.set_task_speed_limit(self.task_id, -5)
        self.assertEqual(self.manager.get(self.task_id).request.speed_limit_bps, 0)

    def test_speed_limit_on_unknown_task_is_noop(self):
        self.manager.set_task_speed_limit("does-not-exist", 1024)  # must not raise

    # ── rename ───────────────────────────────────────────────────────

    def test_rename_queued_task_updates_record(self):
        result = self.manager.rename_task(self.task_id, "renamed.zip")
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "renamed.zip")
        snap = self.manager.get(self.task_id)
        self.assertEqual(snap.filename, "renamed.zip")
        self.assertEqual(snap.request.label, "renamed.zip")

    def test_rename_rejects_path_traversal(self):
        result = self.manager.rename_task(self.task_id, "../../evil.exe")
        self.assertTrue(result["ok"])
        # Only the basename is kept — nothing escapes the download directory.
        self.assertEqual(result["name"], "evil.exe")

    def test_rename_rejects_invalid_name(self):
        result = self.manager.rename_task(self.task_id, "")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_name")

    def test_rename_unknown_task_reports_not_found(self):
        result = self.manager.rename_task("does-not-exist", "x.zip")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_found")

    def test_rename_refused_while_downloading(self):
        with self.manager._lock:
            self.manager._tasks[self.task_id].task.status = TaskStatus.DOWNLOADING
        result = self.manager.rename_task(self.task_id, "nope.zip")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "task_active")

    def test_rename_completed_task_moves_file_on_disk(self):
        old = Path(self.dir) / "orig.zip"
        old.write_bytes(b"x" * SIZE)
        with self.manager._lock:
            rec = self.manager._tasks[self.task_id]
            rec.task.status = TaskStatus.COMPLETED
            rec.task.filename = "orig.zip"
            rec.task.label = "orig.zip"
            self.manager._save_task_locked(rec)

        result = self.manager.rename_task(self.task_id, "final.mkv")
        self.assertTrue(result["ok"], result)
        self.assertTrue((Path(self.dir) / "final.mkv").exists())
        self.assertFalse(old.exists())
        # The resolved path must follow so "Open file" keeps working.
        self.assertEqual(self.manager.get(self.task_id).filename, "final.mkv")

    def test_rename_refuses_to_clobber_existing_file(self):
        (Path(self.dir) / "orig.zip").write_bytes(b"x" * SIZE)
        (Path(self.dir) / "taken.bin").write_bytes(b"y" * SIZE)
        with self.manager._lock:
            rec = self.manager._tasks[self.task_id]
            rec.task.status = TaskStatus.COMPLETED
            rec.task.filename = "orig.zip"
            rec.task.label = "orig.zip"
            self.manager._save_task_locked(rec)

        result = self.manager.rename_task(self.task_id, "taken.bin")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "target_exists")
        # Nothing was destroyed.
        self.assertTrue((Path(self.dir) / "orig.zip").exists())
        self.assertEqual((Path(self.dir) / "taken.bin").read_bytes(), b"y" * SIZE)


if __name__ == "__main__":
    unittest.main()
