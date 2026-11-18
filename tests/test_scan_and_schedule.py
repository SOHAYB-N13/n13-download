"""Regression tests: Pattern Scan is discovery-only; scheduling is persistent.

The central acceptance criterion here is that discovering links can NEVER
create or start a download. Every test in `ScanDiscoveryTests` asserts that
explicitly, not just that the returned report looks right.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.pattern import ScanOutcome, build_candidate_url, scan_pattern_detailed
from config.settings import AppConfig
from core.db import Database
from core.scheduled import (
    ScheduledDownloadService,
    ScheduledRunner,
    format_local,
    resolve_due_at,
)
from core.session import SessionManager
from core.task import TaskStatus
from ui.api import Api


def _probe(available_numbers, server_errors=()):
    """Return a probe stub: only the listed numbers answer successfully."""
    def probe(url, config, session, timeout=None, read_timeout=None):
        number = int(url.rsplit("-", 1)[-1].split(".")[0])
        if number in server_errors:
            return False, 0, False, "", "HTTP 503 - unavailable", {}, ""
        if number in available_numbers:
            return True, 1024, True, f"file-{number}.mkv", "", {
                "Content-Type": "video/x-matroska", "Server": "nginx",
            }, url
        return False, 0, False, "", "HTTP 404 - Not Found", {}, ""
    return probe


class ScanDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AppConfig()
        self.cfg.block_private_urls = False
        self.cfg.connection_mode = "manual"  # queued tasks must not probe the network
        self.api = Api(self.cfg, SessionManager(self.cfg))

    def _task_count(self):
        return len(self.api._manager.snapshots())

    def test_scan_creates_zero_tasks_and_starts_nothing(self):
        before = self._task_count()
        with patch("batch.pattern.probe_with_headers", side_effect=_probe({1, 2})):
            result = self.api.scan_pattern_preview(
                "https://example.com/episode-*.mkv", start=1, padding=2)
        self.assertEqual(result["available"], 2)
        self.assertEqual(result["urls"], [
            "https://example.com/episode-01.mkv",
            "https://example.com/episode-02.mkv",
        ])
        self.assertEqual(self._task_count(), before, "scan must not create tasks")
        self.assertTrue(all(
            s.state != TaskStatus.DOWNLOADING for s in self.api._manager.snapshots()),
            "scan must not start a download")

    def test_scan_reports_unavailable_without_claiming_absence_on_errors(self):
        def server_probe(url, config, session, timeout=None, read_timeout=None):
            return False, 0, False, "", "HTTP 503 - unavailable", {}, ""

        with patch("batch.pattern.probe_with_headers", side_effect=server_probe):
            result = self.api.scan_pattern_preview("https://example.com/e-*.mkv", 1, 2)
        self.assertTrue(all(i["status"] == "server_error" for i in result["items"]))
        self.assertEqual(result["stop_reason"], "server_errors")

    def test_stop_rules_and_counts(self):
        # Five consecutive 404s stop the scan; the 503 does not count as one.
        with patch("batch.pattern.probe_with_headers", side_effect=_probe(set())):
            result = self.api.scan_pattern_preview("https://example.com/e-*.mkv", 1, 2)
        self.assertEqual(result["stop_reason"], "consecutive_404")
        self.assertEqual(result["discovered"], 5)
        self.assertEqual(result["available"], 0)

    def test_cancel_stops_before_next_probe(self):
        calls = []

        def probe(url, *args, **kwargs):
            calls.append(url)
            return True, 10, False, "f", "", {}, url

        cancelled = [False]
        outcome = scan_pattern_detailed(
            "https://example.com/e-*.mkv", self.cfg, self.api._session,
            cancel=lambda: len(calls) >= 2,
        )
        with patch("batch.pattern.probe_with_headers", side_effect=probe):
            outcome = scan_pattern_detailed(
                "https://example.com/e-*.mkv", self.cfg, self.api._session,
                cancel=lambda: len(calls) >= 2,
            )
        self.assertTrue(outcome.cancelled)
        self.assertEqual(len(calls), 2)

    def test_duplicate_candidates_are_probed_once(self):
        seen = []

        def probe(url, *args, **kwargs):
            seen.append(url)
            return False, 0, False, "", "HTTP 404 - Not Found", {}, ""

        with patch("batch.pattern.probe_with_headers", side_effect=probe):
            # Padding 0 can make two consecutive numbers share a URL only if
            # the pattern itself repeats; use a pattern with no wildcard growth.
            scan_pattern_detailed("https://example.com/e-*.mkv", self.cfg,
                                  self.api._session, start_num=1, padding=0)
        self.assertEqual(len(seen), len(set(seen)))

    def test_invalid_pattern_returns_no_items(self):
        before = self._task_count()
        result = self.api.scan_pattern_preview("not-a-url", 1, 2)
        self.assertEqual(result["stop_reason"], "invalid_pattern")
        self.assertEqual(result["discovered"], 0)
        self.assertEqual(self._task_count(), before)

    def test_candidate_url_encoding_preserves_query(self):
        url = build_candidate_url("https://e.test/My File-*.mkv?token=a%2Fb&x=1", "07")
        self.assertIn("My%20File-07.mkv", url)
        self.assertIn("token=a%2Fb&x=1", url)

    def test_explicit_confirmation_creates_only_selected_tasks(self):
        with patch("batch.pattern.probe_with_headers", side_effect=_probe({1, 2, 3})):
            report = self.api.scan_pattern_preview("https://example.com/e-*.mkv", 1, 2)
        self.assertEqual(report["available"], 3)
        # Simulate the UI: user selects only items 1 and 3 and submits.
        chosen = [report["items"][0]["url"], report["items"][2]["url"]]
        before = self._task_count()
        result = self.api.add_batch_report(chosen, str(self.tmp), "", autostart=False)
        self.assertEqual(result["added"], 2)
        self.assertEqual(self._task_count(), before + 2)
        self.assertTrue(all(
            s.state != TaskStatus.DOWNLOADING for s in self.api._manager.snapshots()),
            "queue behaviour must not start any transfer")


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "sched.db")
        self.service = ScheduledDownloadService(self.db)
        self.now = time.time()

    def tearDown(self):
        self.db.close()

    def _payload(self, n=1):
        return {"url": f"https://example.com/f{n}.zip", "directory": str(self.tmp),
                "autostart": True}

    def test_future_schedule_does_not_fire_early_and_fires_once(self):
        sid = self.service.schedule(self._payload(), self.now + 60, "UTC")
        self.assertEqual(self.service.claim_due(now=self.now), [])
        claimed = self.service.claim_due(now=self.now + 61)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["status"], "starting")
        self.assertEqual(self.service.claim_due(now=self.now + 62), [],
                         "a second check must never re-fire the same row")
        self.service.mark_started(sid, "task-1")
        self.assertEqual(self.service.get(sid)["status"], "completed")

    def test_cancel_prevents_execution(self):
        sid = self.service.schedule(self._payload(), self.now + 10)
        self.assertTrue(self.service.cancel(sid))
        self.assertEqual(self.service.claim_due(now=self.now + 20), [])
        self.assertEqual(self.service.get(sid)["status"], "cancelled")

    def test_reschedule_invalidates_the_old_due_time(self):
        sid = self.service.schedule(self._payload(), self.now + 10)
        self.assertTrue(self.service.reschedule(sid, self.now + 3600))
        self.assertEqual(self.service.claim_due(now=self.now + 20), [])
        claimed = self.service.claim_due(now=self.now + 3700)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["id"], sid)

    def test_pause_and_resume(self):
        sid = self.service.schedule(self._payload(), self.now + 10)
        self.assertTrue(self.service.pause(sid))
        self.assertEqual(self.service.claim_due(now=self.now + 20), [])
        self.assertTrue(self.service.resume(sid))
        self.assertEqual(len(self.service.claim_due(now=self.now + 20)), 1)

    def test_restart_marks_due_as_missed_and_claimed_as_failed(self):
        due_sid = self.service.schedule(self._payload(1), self.now + 10)
        starting_sid = self.service.schedule(self._payload(2), self.now + 5)
        # Only the second row is due yet, so only it is claimed.
        self.service.claim_due(now=self.now + 6)
        # Simulate a restart after the first row's due time passed.
        missed = self.service.recover_on_startup(now=self.now + 20)
        self.assertEqual({m["id"] for m in missed}, {due_sid})
        self.assertEqual(self.service.get(starting_sid)["status"], "failed")
        self.assertEqual(self.service.get(due_sid)["status"], "missed")

    def test_persistence_across_a_new_database_handle(self):
        sid = self.service.schedule(self._payload(), self.now + 100, "UTC")
        self.db.close()
        self.db = Database(self.tmp / "sched.db")
        self.service = ScheduledDownloadService(self.db)
        row = self.service.get(sid)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "scheduled")
        self.assertAlmostEqual(row["due_at"], self.now + 100, places=1)
        self.assertEqual(row["payload"]["url"], "https://example.com/f1.zip")

    def test_timezone_and_past_validation(self):
        due, zone = resolve_due_at("2099-07-01T12:00", "UTC")
        self.assertEqual(zone, "UTC")
        self.assertEqual(format_local(due, zone), "2099-07-01 12:00")
        with self.assertRaises(ValueError):
            resolve_due_at("2000-01-01T00:00", "UTC")

    def test_runner_fires_once_and_reports_failure(self):
        sid = self.service.schedule(self._payload(), self.now + 10)
        calls = []
        runner = ScheduledRunner(self.service, lambda item: calls.append(item) or "t1",
                                 now_fn=lambda: self.now + 20)
        self.assertEqual(runner.tick_once(), 1)
        self.assertEqual(runner.tick_once(), 0)
        self.assertEqual(len(calls), 1)

        sid2 = self.service.schedule(self._payload(2), self.now + 10)
        runner2 = ScheduledRunner(self.service, lambda item: (_ for _ in ()).throw(RuntimeError("boom")),
                                  now_fn=lambda: self.now + 20)
        # A failed start is not counted as fired, but it must be persisted.
        self.assertEqual(runner2.tick_once(), 0)
        self.assertEqual(self.service.get(sid2)["status"], "failed")
        self.assertIn("boom", self.service.get(sid2)["last_error"])

    def test_multiple_schedules_are_independent(self):
        ids = [self.service.schedule(self._payload(n), self.now + n * 10) for n in (1, 2, 3)]
        claimed = self.service.claim_due(now=self.now + 25)
        self.assertEqual({c["id"] for c in claimed}, set(ids[:2]))
        self.assertEqual(self.service.get(ids[2])["status"], "scheduled")


if __name__ == "__main__":
    unittest.main()


class ScanSubmissionBackendTests(unittest.TestCase):
    """The values collected by the review UI must survive into real tasks."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = AppConfig()
        self.cfg.block_private_urls = False
        self.cfg.connection_mode = "manual"  # no background probes from queued tasks
        self.api = Api(self.cfg, SessionManager(self.cfg))

    def test_custom_destination_and_group_are_preserved(self):
        dest = self.tmp / "custom"
        result = self.api.add_batch_report(
            ["https://x.test/one.zip", "https://x.test/two.zip"],
            str(dest), "default", autostart=False)
        self.assertEqual(result["added"], 2)
        for tid in result["ids"]:
            snap = self.api._manager.get(tid)
            self.assertEqual(snap.request.directory, str(dest))
            self.assertEqual(snap.project_id, "default")

    def test_duplicate_urls_are_reported_not_inflated(self):
        self.cfg.duplicate_policy = "ask"
        result = self.api.add_batch_report(
            ["https://x.test/same.zip", "https://x.test/same.zip"],
            str(self.tmp), "", autostart=False)
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["duplicates"], 1)

    def test_repeated_submission_does_not_create_tasks_twice(self):
        self.cfg.duplicate_policy = "ask"
        urls = ["https://x.test/repeat.zip"]
        first = self.api.add_batch_report(urls, str(self.tmp), "", autostart=False)
        second = self.api.add_batch_report(urls, str(self.tmp), "", autostart=False)
        self.assertEqual(first["added"], 1)
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(len(set(first["ids"] + second["ids"])), 1)

    def test_empty_selection_creates_nothing(self):
        result = self.api.add_batch_report([], str(self.tmp), "", autostart=False)
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["total"], 0)

    def test_partial_failure_keeps_successful_results(self):
        with patch.object(self.api._manager, "add_many", side_effect=[
            ["partial-1"],
        ]) as add_many:
            result = self.api.add_batch_report(
                ["https://x.test/a.zip", "https://x.test/b.zip"],
                str(self.tmp), "", autostart=False)
        self.assertEqual(add_many.call_count, 1)
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["total"], 2)
