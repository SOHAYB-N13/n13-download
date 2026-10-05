"""Phase B — DownloadTask state machine tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.task import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    DownloadTask,
    TaskStatus,
    TransitionError,
    is_active,
    is_terminal,
    normalize_status,
)


class StateMachineTest(unittest.TestCase):
    def test_initial_state(self):
        t = DownloadTask()
        self.assertEqual(t.status, TaskStatus.QUEUED)
        self.assertTrue(t.can_transition(TaskStatus.ANALYZING))

    def test_full_lifecycle(self):
        t = DownloadTask()
        t.mark_analyzing()
        self.assertEqual(t.status, TaskStatus.ANALYZING)
        t.mark_starting()
        self.assertIsNotNone(t.started_at)
        t.mark_downloading()
        t.mark_merging()
        t.mark_verifying()
        t.mark_completed()
        self.assertEqual(t.status, TaskStatus.COMPLETED)
        self.assertIsNotNone(t.completed_at)

    def test_pause_resume(self):
        t = DownloadTask()
        t.mark_analyzing()
        t.mark_starting()
        t.mark_downloading()
        t.mark_paused()
        self.assertEqual(t.status, TaskStatus.PAUSED)
        t.mark_downloading()
        self.assertEqual(t.status, TaskStatus.DOWNLOADING)

    def test_invalid_transition_rejected(self):
        t = DownloadTask()
        # COMPLETED -> DOWNLOADING is illegal
        t.force_status(TaskStatus.COMPLETED)
        with self.assertRaises(TransitionError):
            t.mark_downloading()
        # QUEUED -> DOWNLOADING directly is illegal
        t2 = DownloadTask()
        with self.assertRaises(TransitionError):
            t2.mark_downloading()

    def test_transition_is_strict_not_silent(self):
        t = DownloadTask()
        t.force_status(TaskStatus.DOWNLOADING)
        t.mark_failed("boom")
        self.assertEqual(t.status, TaskStatus.FAILED)
        # FAILED -> DOWNLOADING illegal
        with self.assertRaises(TransitionError):
            t.mark_downloading()
        # FAILED -> QUEUED (retry) is legal
        t.requeue(reason="retry")
        self.assertEqual(t.status, TaskStatus.QUEUED)

    def test_redownload_from_completed(self):
        t = DownloadTask()
        t.mark_analyzing()
        t.mark_starting()
        t.mark_downloading()
        t.mark_merging()
        t.mark_verifying()
        t.mark_completed()
        t.requeue(reason="redownload")
        self.assertEqual(t.status, TaskStatus.QUEUED)

    def test_cancel_from_any_active_state(self):
        for start in (TaskStatus.ANALYZING, TaskStatus.STARTING,
                      TaskStatus.DOWNLOADING, TaskStatus.MERGING,
                      TaskStatus.VERIFYING):
            t = DownloadTask()
            if start in (TaskStatus.ANALYZING,):
                t.force_status(start)
            else:
                t.force_status(start)
            t.mark_cancelled()
            self.assertEqual(t.status, TaskStatus.CANCELLED)

    def test_no_scattered_booleans(self):
        """Tasks expose a single status; no boolean flag drift is possible."""
        t = DownloadTask()
        self.assertFalse(hasattr(t, "cancelled"))
        self.assertFalse(hasattr(t, "paused"))
        self.assertFalse(hasattr(t, "done"))

    def test_normalize_status_legacy(self):
        self.assertIs(normalize_status("Stopped"), TaskStatus.CANCELLED)
        self.assertIs(normalize_status("Stopping"), TaskStatus.DOWNLOADING)
        self.assertIs(normalize_status("Complete"), TaskStatus.COMPLETED)
        self.assertIs(normalize_status("Downloading"), TaskStatus.DOWNLOADING)
        self.assertIs(normalize_status("garbage"), TaskStatus.QUEUED)

    def test_speed_and_eta(self):
        import time
        t = DownloadTask()
        t.total_size = 1000
        t.started_at = time.time() - 10
        t.update_speed(200)                       # first sample seeds the window
        time.sleep(0.25)                          # comfortably above the 0.05 s sample gate
        t.update_speed(500)                       # now a real speed exists
        self.assertEqual(t.downloaded_size, 500)
        self.assertGreater(t.current_speed, 0)
        self.assertGreater(t.average_speed, 0)
        self.assertIsNotNone(t.eta_seconds)
        self.assertGreater(t.eta_seconds, 0)
        t.update_speed(1000)
        self.assertEqual(t.eta_seconds, 0.0)
        self.assertAlmostEqual(t.percent, 100.0)

    def test_serialization_roundtrip(self):
        t = DownloadTask(url="https://x/f.zip", directory="C:/dl")
        t.mark_analyzing()
        t.mark_starting()
        t.mark_downloading()
        d = t.to_dict()
        t2 = DownloadTask.from_dict(d)
        self.assertEqual(t2.status, TaskStatus.DOWNLOADING)
        self.assertEqual(t2.url, t.url)
        self.assertEqual(t2.downloaded_size, t.downloaded_size)

    def test_states_are_consistent(self):
        # Every status is either active, terminal, or queued/removed.
        for s in TaskStatus:
            self.assertTrue(is_active(s) or is_terminal(s) or s in (
                TaskStatus.QUEUED, TaskStatus.REMOVED,
            ))


if __name__ == "__main__":
    unittest.main()
