"""UI queue ordering, priority, and extension repair.

Covers the backend capabilities the redesigned Downloads and Browser pages
rely on:

* ``TaskSnapshot.queue_index`` — the queue position the UI needs in order to
  offer an honest "Queue order" sort and Move up/down controls.  Without it a
  reorder is invisible, which is exactly the trap this field removes.
* ``TaskManager.move_task`` — must reorder, must clamp at both ends, and must
  announce the tasks whose position shifted so the UI can refresh.
* ``browser.extension_locator.repair_extension`` — must rebuild a valid,
  token-synced copy and must report (never raise) when it cannot.

Uses a fake runner (no network) and a temporary application root so the real
project's ``chrome_extension`` folder is never touched.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import browser.extension_locator as ext_loc
from ui.common import DownloadRequest, TaskManager

REPO_ROOT = Path(__file__).resolve().parent.parent
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


class QueueOrderTests(unittest.TestCase):
    """The Downloads list's Queue-order view and Move up/down controls."""

    def setUp(self) -> None:
        # mkdtemp (not TemporaryDirectory): the SQLite store keeps the db file
        # open, which makes auto-rmtree fail on Windows.  close() in tearDown
        # releases the handle first.
        self.dir = tempfile.mkdtemp()
        self.manager = TaskManager(FakeRunner(), Path(self.dir), max_concurrent=1)
        self.events: list[tuple[str, object]] = []
        self.manager.subscribe(lambda ev, snap: self.events.append((ev, snap)))
        # autostart=False keeps every task deterministically QUEUED so the
        # worker cannot race ahead and drain the queue mid-assertion.
        self.ids = [
            self.manager.add(
                DownloadRequest(url=f"https://example.com/{n}.zip", directory=self.dir, label=f"{n}.zip"),
                autostart=False,
            )
            for n in ("a", "b", "c")
        ]
        self.by_label = dict(zip(("a", "b", "c"), self.ids))

    def tearDown(self) -> None:
        try:
            self.manager.close()
        except Exception:
            pass

    def _indexes(self) -> list[int]:
        """Queue position of a, b, c in that order."""
        return [self.manager.get(self.by_label[n]).to_dict()["queue_index"] for n in ("a", "b", "c")]

    def _ordered_labels(self) -> list[str]:
        """Task labels in queue order, position 0 first."""
        pairs = [(self.manager.get(tid).to_dict()["queue_index"], label) for label, tid in self.by_label.items()]
        return [label for _, label in sorted(pairs)]

    def test_queue_index_is_exposed_and_ordered(self):
        # The frontend sorts on this field, so it must be present and dense.
        self.assertEqual(self._indexes(), [0, 1, 2])
        self.assertEqual(self._ordered_labels(), ["a", "b", "c"])
        for tid in self.ids:
            self.assertIn("queue_index", self.manager.get(tid).to_dict())

    def test_move_down_swaps_positions(self):
        self.manager.move_task(self.by_label["a"], 1)
        self.assertEqual(self._ordered_labels(), ["b", "a", "c"])
        self.assertEqual(self._indexes(), [1, 0, 2])

    def test_move_up_swaps_positions(self):
        self.manager.move_task(self.by_label["c"], -1)
        self.assertEqual(self._ordered_labels(), ["a", "c", "b"])
        self.assertEqual(self._indexes(), [0, 2, 1])

    def test_move_emits_updated_for_shifted_tasks(self):
        self.events.clear()
        self.manager.move_task(self.by_label["a"], 1)
        moved = {snap.id for ev, snap in self.events if ev == "updated"}
        # Both endpoints of the swap changed position, so both must be
        # announced — otherwise the UI keeps showing stale row order.
        self.assertEqual(moved, {self.by_label["a"], self.by_label["b"]})

    def test_move_clamps_at_both_ends(self):
        # Already first: moving up must be a no-op, and must stay silent.
        self.events.clear()
        self.manager.move_task(self.by_label["a"], -1)
        self.assertEqual(self._ordered_labels(), ["a", "b", "c"])
        self.assertEqual([e for e, _ in self.events], [])

        self.manager.move_task(self.by_label["c"], 1)
        self.assertEqual(self._ordered_labels(), ["a", "b", "c"])

    def test_move_unknown_task_is_noop(self):
        self.events.clear()
        self.manager.move_task("does-not-exist", -1)  # must not raise
        self.assertEqual(self._ordered_labels(), ["a", "b", "c"])
        self.assertEqual([e for e, _ in self.events], [])

    def test_repeated_single_steps_walk_a_task_to_the_front(self):
        # The group move in the UI issues one move_task per selected id, so
        # repeated single steps must accumulate exactly.
        self.manager.move_task(self.by_label["c"], -1)
        self.manager.move_task(self.by_label["c"], -1)
        self.assertEqual(self._ordered_labels(), ["c", "a", "b"])
        self.assertEqual(self._indexes(), [1, 2, 0])


class PriorityTests(unittest.TestCase):
    """Priority is persisted and surfaced, since the UI edits it directly."""

    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.manager = TaskManager(FakeRunner(), Path(self.dir), max_concurrent=1)
        self.task_id = self.manager.add(
            DownloadRequest(url="https://example.com/a.zip", directory=self.dir, label="a.zip"),
            autostart=False,
        )

    def tearDown(self) -> None:
        try:
            self.manager.close()
        except Exception:
            pass

    def test_priority_is_exposed_and_clamped(self):
        self.manager.set_priority(self.task_id, 1)
        self.assertEqual(self.manager.get(self.task_id).to_dict()["priority"], 1)

        # 0 = highest, 10 = lowest; anything outside that must be clamped
        # rather than rejected, so a slider can never wedge the queue.
        self.manager.set_priority(self.task_id, 99)
        self.assertEqual(self.manager.get(self.task_id).to_dict()["priority"], 10)
        self.manager.set_priority(self.task_id, -4)
        self.assertEqual(self.manager.get(self.task_id).to_dict()["priority"], 0)

    def test_priority_on_unknown_task_is_noop(self):
        self.manager.set_priority("does-not-exist", 3)  # must not raise


class ExtensionRepairTests(unittest.TestCase):
    """Browser page → Repair extension.

    ``app_root`` is redirected to a throwaway directory holding a copy of the
    bundled template, so the real project's ``chrome_extension`` folder is
    never deleted or rewritten by a test run.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "app"
        self.root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(REPO_ROOT / "extension", self.root / "extension")
        self._orig_app_root = ext_loc.app_root
        ext_loc.app_root = lambda: self.root

    def tearDown(self) -> None:
        ext_loc.app_root = self._orig_app_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_repair_rebuilds_a_valid_copy(self):
        result = ext_loc.repair_extension()
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertTrue(result["path"])

        target = Path(result["path"])
        self.assertTrue(target.is_dir())
        # It must land inside the redirected root, never the real project.
        self.assertTrue(str(target).startswith(str(self.root)))
        ok, reason = ext_loc.validate_extension_dir(target)
        self.assertTrue(ok, reason)

    def test_repair_replaces_a_damaged_copy(self):
        # Simulate the real-world breakage: files referenced by the manifest
        # are gone.  Repair must not merely report it — it must fix it.
        broken = self.root / "chrome_extension"
        shutil.copytree(REPO_ROOT / "extension", broken)
        for js in broken.glob("*.js"):
            js.unlink()
        self.assertFalse(ext_loc.validate_extension_dir(broken)[0])

        result = ext_loc.repair_extension()
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertTrue(ext_loc.validate_extension_dir(Path(result["path"]))[0])

    def test_repair_reports_instead_of_raising_without_a_template(self):
        # No extension/ template at all: the UI button must still get a
        # structured answer rather than an exception it cannot render.
        shutil.rmtree(self.root / "extension")
        result = ext_loc.repair_extension()
        self.assertFalse(result["ok"])
        self.assertEqual(result["path"], "")
        self.assertTrue(result["reason"])

    def test_repair_emits_progress_lines(self):
        lines: list[str] = []
        ext_loc.repair_extension(emit=lines.append)
        self.assertTrue(lines, "repair should report progress to the UI log")


if __name__ == "__main__":
    unittest.main()
