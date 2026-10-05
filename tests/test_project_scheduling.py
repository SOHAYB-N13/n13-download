"""Project layer — schedule windows and the admission policy.

Two rules carry the weight here:

* an overnight window (23:00 → 07:00) must be open at 03:00, and that only works
  if the evaluator looks at the window that opened *yesterday*;
* day indices are the JavaScript convention (Sunday = 0), because that is what
  the UI sends.  ``datetime.weekday()`` is Monday = 0, so a naive comparison
  shifts every schedule by one day — silently, and only on day-filtered windows.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from projects.admission import (
    REASON_CLOSED,
    REASON_GLOBAL_LIMIT,
    REASON_OK,
    REASON_OUTSIDE_SCHEDULE,
    REASON_PROJECT_LIMIT,
    REASON_PROJECT_PAUSED,
    AdmissionContext,
    ProjectGate,
    effective_project_limit,
    may_start,
)
from projects.schedule import (
    REASON_AFTER_STOP,
    REASON_BEFORE_START,
    REASON_DAY_NOT_ALLOWED,
    REASON_INVALID,
    REASON_IN_WINDOW,
    REASON_NO_SCHEDULE,
    ProjectSchedule,
    days_from_json,
    days_to_json,
    evaluate,
    format_hhmm,
    is_open,
    normalize_days,
    parse_hhmm,
)

# 2026-10-05 is a Monday.  Anchoring on a fixed date keeps every assertion below
# readable and makes the weekday convention explicit rather than incidental.
MONDAY = datetime(2026, 10, 5, 12, 0)
TUESDAY = datetime(2026, 10, 6, 12, 0)
SATURDAY = datetime(2026, 10, 10, 12, 0)
SUNDAY = datetime(2026, 10, 11, 12, 0)


def at(moment: datetime, hour: int, minute: int = 0) -> datetime:
    return moment.replace(hour=hour, minute=minute)


class TimeParsingTest(unittest.TestCase):
    def test_hhmm_is_parsed(self):
        self.assertEqual(parse_hhmm("23:00"), 1380)
        self.assertEqual(parse_hhmm("00:00"), 0)
        self.assertEqual(parse_hhmm("07:30"), 450)

    def test_single_digit_hours_are_accepted(self):
        self.assertEqual(parse_hhmm("7:05"), 425)

    def test_an_integer_is_already_minutes(self):
        self.assertEqual(parse_hhmm(1380), 1380)

    def test_nonsense_is_rejected(self):
        for bad in ("", "  ", "25:00", "12:60", "noon", "12", "12:34:56", None, True):
            self.assertIsNone(parse_hhmm(bad), f"{bad!r} should not parse")

    def test_formatting_round_trips(self):
        for minutes in (0, 425, 1380, 1439):
            self.assertEqual(parse_hhmm(format_hhmm(minutes)), minutes)

    def test_formatting_a_missing_value_is_empty(self):
        self.assertEqual(format_hhmm(None), "")


class DayNormalisationTest(unittest.TestCase):
    def test_days_are_sorted_and_deduplicated(self):
        self.assertEqual(normalize_days([6, 0, 6, 3]), [0, 3, 6])

    def test_out_of_range_days_are_dropped(self):
        self.assertEqual(normalize_days([-1, 7, 99, 2]), [2])

    def test_a_non_iterable_is_no_days(self):
        self.assertEqual(normalize_days(None), [])
        self.assertEqual(normalize_days("0,1"), [])

    def test_json_round_trip(self):
        self.assertEqual(days_from_json(days_to_json([1, 0])), [0, 1])

    def test_broken_json_is_no_days(self):
        self.assertEqual(days_from_json("{not json"), [])
        self.assertEqual(days_from_json(""), [])


class WindowEvaluationTest(unittest.TestCase):
    def test_a_disabled_schedule_is_always_open(self):
        decision = evaluate(ProjectSchedule(enabled=False), MONDAY)
        self.assertTrue(decision.open)
        self.assertEqual(decision.reason, REASON_NO_SCHEDULE)

    def test_an_unparseable_window_fails_open(self):
        """A window this build cannot read must never stop downloads forever."""
        schedule = ProjectSchedule(enabled=True, start="garbage", stop="07:00")
        decision = evaluate(schedule, MONDAY)
        self.assertTrue(decision.open)
        self.assertEqual(decision.reason, REASON_INVALID)

    def test_inside_a_daytime_window(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00")
        self.assertTrue(is_open(schedule, at(MONDAY, 12)))
        self.assertEqual(evaluate(schedule, at(MONDAY, 12)).reason, REASON_IN_WINDOW)

    def test_before_a_daytime_window(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00")
        decision = evaluate(schedule, at(MONDAY, 7))
        self.assertFalse(decision.open)
        self.assertEqual(decision.reason, REASON_BEFORE_START)

    def test_after_a_daytime_window(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00")
        decision = evaluate(schedule, at(MONDAY, 20))
        self.assertFalse(decision.open)
        self.assertEqual(decision.reason, REASON_AFTER_STOP)

    def test_the_window_boundaries_are_inclusive_at_the_start(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00")
        self.assertTrue(is_open(schedule, at(MONDAY, 9, 0)))
        self.assertFalse(is_open(schedule, at(MONDAY, 17, 0)), "the stop minute is exclusive")

    def test_a_closed_window_reports_when_it_reopens(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00")
        decision = evaluate(schedule, at(MONDAY, 7))
        self.assertIsNotNone(decision.next_change_at)
        reopened = datetime.fromtimestamp(decision.next_change_at)
        self.assertEqual((reopened.hour, reopened.minute), (9, 0))
        self.assertEqual(reopened.date(), MONDAY.date())

    def test_an_open_window_reports_when_it_closes(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00")
        decision = evaluate(schedule, at(MONDAY, 12))
        self.assertIsNotNone(decision.closes_at)
        closes = datetime.fromtimestamp(decision.closes_at)
        self.assertEqual((closes.hour, closes.minute), (17, 0))


class OvernightWindowTest(unittest.TestCase):
    """23:00 → 07:00 must be open at 03:00 the next morning."""

    def setUp(self):
        self.schedule = ProjectSchedule(enabled=True, start="23:00", stop="07:00")

    def test_open_late_at_night(self):
        self.assertTrue(is_open(self.schedule, at(MONDAY, 23, 30)))

    def test_open_after_midnight(self):
        self.assertTrue(is_open(self.schedule, at(TUESDAY, 3, 0)),
                        "the window that contains 03:00 opened the previous evening")

    def test_still_open_just_before_the_stop(self):
        self.assertTrue(is_open(self.schedule, at(TUESDAY, 6, 59)))

    def test_closed_at_the_stop_minute(self):
        self.assertFalse(is_open(self.schedule, at(TUESDAY, 7, 0)))

    def test_closed_during_the_day(self):
        self.assertFalse(is_open(self.schedule, at(TUESDAY, 12, 0)))

    def test_the_window_is_recognised_as_overnight(self):
        self.assertTrue(self.schedule.is_overnight)

    def test_closing_time_belongs_to_the_next_day(self):
        decision = evaluate(self.schedule, at(MONDAY, 23, 30))
        self.assertIsNotNone(decision.closes_at)
        closes = datetime.fromtimestamp(decision.closes_at)
        self.assertEqual(closes.date(), TUESDAY.date())
        self.assertEqual(closes.hour, 7)

    def test_the_startup_inside_the_window_case(self):
        """Launching the app at 03:00 must not wait for the next evening."""
        self.assertTrue(is_open(self.schedule, at(TUESDAY, 3, 0)))

    def test_the_startup_outside_the_window_case(self):
        decision = evaluate(self.schedule, at(TUESDAY, 12, 0))
        self.assertFalse(decision.open)
        reopened = datetime.fromtimestamp(decision.next_change_at)
        self.assertEqual(reopened.date(), TUESDAY.date())
        self.assertEqual(reopened.hour, 23)


class DayFilterTest(unittest.TestCase):
    """Day indices are Sunday = 0, matching what the UI sends."""

    def test_a_window_with_no_days_runs_every_day(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00")
        for moment in (MONDAY, TUESDAY, SATURDAY, SUNDAY):
            self.assertTrue(is_open(schedule, at(moment, 12)), moment.strftime("%A"))

    def test_sunday_is_index_zero(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00", days=[0])
        self.assertTrue(is_open(schedule, at(SUNDAY, 12)), "index 0 must mean Sunday")
        self.assertFalse(is_open(schedule, at(MONDAY, 12)), "index 0 must not mean Monday")

    def test_monday_is_index_one(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00", days=[1])
        self.assertTrue(is_open(schedule, at(MONDAY, 12)), "index 1 must mean Monday")
        self.assertFalse(is_open(schedule, at(SUNDAY, 12)))

    def test_saturday_is_index_six(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00", days=[6])
        self.assertTrue(is_open(schedule, at(SATURDAY, 12)))
        self.assertFalse(is_open(schedule, at(SUNDAY, 12)))

    def test_a_disallowed_day_says_so(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00", days=[0])
        decision = evaluate(schedule, at(MONDAY, 12))
        self.assertFalse(decision.open)
        self.assertEqual(decision.reason, REASON_DAY_NOT_ALLOWED)

    def test_a_disallowed_day_reports_the_next_opening(self):
        schedule = ProjectSchedule(enabled=True, start="09:00", stop="17:00", days=[0])
        decision = evaluate(schedule, at(MONDAY, 12))
        nxt = datetime.fromtimestamp(decision.next_change_at)
        self.assertEqual(nxt.date(), SUNDAY.date())

    def test_an_overnight_window_respects_the_day_it_opens(self):
        """Friday 23:00 → 07:00 covers Saturday morning, because Friday opened it."""
        schedule = ProjectSchedule(
            enabled=True, start="23:00", stop="07:00", days=[5],  # 5 = Friday
        )
        self.assertTrue(is_open(schedule, at(SATURDAY, 3)), "Saturday 03:00 is inside Friday's window")
        self.assertFalse(is_open(schedule, at(SUNDAY, 3)), "Sunday 03:00 is not")


class AllDayWindowTest(unittest.TestCase):
    def test_equal_start_and_stop_is_a_24_hour_window(self):
        schedule = ProjectSchedule(enabled=True, start="00:00", stop="00:00")
        for hour in (0, 6, 12, 18, 23):
            self.assertTrue(is_open(schedule, at(MONDAY, hour)), f"{hour}:00")

    def test_a_24_hour_window_is_still_bounded_by_the_day_filter(self):
        schedule = ProjectSchedule(enabled=True, start="00:00", stop="00:00", days=[1])
        self.assertTrue(is_open(schedule, at(MONDAY, 3)))
        self.assertFalse(is_open(schedule, at(SUNDAY, 3)))


class ScheduleSerialisationTest(unittest.TestCase):
    def test_to_dict_and_from_dict_round_trip(self):
        original = ProjectSchedule(enabled=True, start="23:00", stop="07:00", days=[0, 6])
        restored = ProjectSchedule.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_from_dict_tolerates_rubbish(self):
        schedule = ProjectSchedule.from_dict({"enabled": "yes", "start": None, "days": "nope"})
        self.assertTrue(schedule.enabled)
        self.assertIsNone(schedule.start)
        self.assertEqual(schedule.days, [])

    def test_from_dict_of_none_is_a_disabled_schedule(self):
        schedule = ProjectSchedule.from_dict(None)
        self.assertFalse(schedule.enabled)

    def test_from_row_reads_the_database_shape(self):
        schedule = ProjectSchedule.from_row({
            "schedule_enabled": 1,
            "schedule_start": "23:00",
            "schedule_stop": "07:00",
            "schedule_days": "[0, 6]",
        })
        self.assertTrue(schedule.enabled)
        self.assertEqual(schedule.days, [0, 6])


# --------------------------------------------------------------------------- #
# Admission policy
# --------------------------------------------------------------------------- #


def ctx(**overrides) -> AdmissionContext:
    base = dict(
        global_active=0,
        global_limit=3,
        project_active=0,
        project_limit=0,
        project_paused=False,
        window_open=True,
    )
    base.update(overrides)
    return AdmissionContext(**base)


class AdmissionPolicyTest(unittest.TestCase):
    def test_an_idle_project_may_start(self):
        decision = may_start(ctx())
        self.assertTrue(decision.allowed)
        self.assertTrue(bool(decision))
        self.assertEqual(decision.reason, REASON_OK)

    def test_a_closed_queue_blocks_everything(self):
        decision = may_start(ctx(), closed=True)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, REASON_CLOSED)

    def test_a_paused_project_gets_no_new_work(self):
        decision = may_start(ctx(project_paused=True))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, REASON_PROJECT_PAUSED)

    def test_a_closed_window_blocks_its_own_project(self):
        decision = may_start(ctx(window_open=False))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, REASON_OUTSIDE_SCHEDULE)

    def test_the_global_limit_is_reported_before_the_project_limit(self):
        """A full pool is the truthful reason for every project.

        Reporting the project limit there would send the user to the wrong
        setting, so the harder constraint is tested first.
        """
        decision = may_start(ctx(global_active=3, global_limit=3, project_active=5, project_limit=2))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, REASON_GLOBAL_LIMIT)

    def test_a_project_limit_of_zero_inherits_the_global_one(self):
        self.assertEqual(effective_project_limit(0, 4), 4)
        self.assertEqual(effective_project_limit(-2, 4), 4)
        self.assertEqual(effective_project_limit(2, 4), 2)

    def test_a_project_ceiling_is_enforced(self):
        decision = may_start(ctx(project_active=2, project_limit=2))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, REASON_PROJECT_LIMIT)

    def test_a_project_may_start_while_under_its_ceiling(self):
        self.assertTrue(may_start(ctx(project_active=1, project_limit=2)).allowed)

    def test_an_inheriting_project_is_only_bounded_by_the_global_limit(self):
        self.assertTrue(may_start(ctx(global_active=2, global_limit=3, project_active=2, project_limit=0)).allowed)
        self.assertFalse(may_start(ctx(global_active=3, global_limit=3, project_active=3, project_limit=0)).allowed)

    def test_the_project_pause_is_reported_before_a_full_pool(self):
        decision = may_start(ctx(project_paused=True, global_active=9, global_limit=3))
        self.assertEqual(decision.reason, REASON_PROJECT_PAUSED)

    def test_a_decision_serialises(self):
        payload = may_start(ctx()).to_dict()
        self.assertEqual(payload, {"allowed": True, "reason": REASON_OK})


class ProjectGateTest(unittest.TestCase):
    def test_a_gate_round_trips(self):
        gate = ProjectGate(paused=True, window_open=False, limit=3)
        restored = ProjectGate.coerce(gate.to_dict())
        self.assertEqual(restored, gate)

    def test_coercing_none_gives_a_permissive_gate(self):
        """A project with no gate must be allowed to run, not silently frozen."""
        gate = ProjectGate.coerce(None)
        self.assertFalse(gate.paused)
        self.assertTrue(gate.window_open)
        self.assertEqual(gate.limit, 0)

    def test_coercing_a_partial_mapping_fills_the_defaults(self):
        gate = ProjectGate.coerce({"paused": True})
        self.assertTrue(gate.paused)
        self.assertTrue(gate.window_open)


if __name__ == "__main__":
    unittest.main()
