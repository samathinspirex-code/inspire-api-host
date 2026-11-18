import unittest
from datetime import datetime, timedelta, timezone

from app.modules.lms.attendance_service import _attendance_status, _merge_duration


class AttendanceCalculationTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        self.end = self.start + timedelta(hours=1)

    def test_overlapping_device_sessions_are_not_double_counted(self):
        result = _merge_duration(
            [
                (self.start, self.start + timedelta(minutes=30)),
                (self.start + timedelta(minutes=10), self.start + timedelta(minutes=40)),
            ],
            self.start,
            self.end,
        )
        self.assertEqual(result[0], 40 * 60)

    def test_disconnected_sessions_are_added(self):
        result = _merge_duration(
            [
                (self.start, self.start + timedelta(minutes=20)),
                (self.start + timedelta(minutes=30), self.start + timedelta(minutes=45)),
            ],
            self.start,
            self.end,
        )
        self.assertEqual(result[0], 35 * 60)

    def test_fifty_percent_is_present(self):
        self.assertEqual(_attendance_status(30 * 60, 60 * 60, 50), "present")

    def test_below_fifty_percent_is_absent(self):
        self.assertEqual(_attendance_status(29 * 60, 60 * 60, 50), "absent")


if __name__ == "__main__":
    unittest.main()


class ScheduledDurationRuleTests(unittest.TestCase):
    """Attendance is judged against the scheduled class length at 75%."""

    def setUp(self):
        from app.modules.lms.attendance_service import scheduled_class_seconds

        self.scheduled_class_seconds = scheduled_class_seconds
        self.start = datetime(2026, 11, 20, 1, 30, tzinfo=timezone.utc)
        self.end = self.start + timedelta(hours=1)

    def test_scheduled_length_is_the_base(self):
        self.assertEqual(self.scheduled_class_seconds(self.start, self.end, 999), 3600)

    def test_falls_back_when_schedule_is_unusable(self):
        self.assertEqual(self.scheduled_class_seconds(self.end, self.start, 1800), 1800)
        self.assertEqual(self.scheduled_class_seconds(None, None, 0), 1)

    def test_45_of_60_minutes_is_present(self):
        base = self.scheduled_class_seconds(self.start, self.end)
        self.assertEqual(_attendance_status(45 * 60, base, 75), "present")

    def test_40_of_60_minutes_is_absent(self):
        base = self.scheduled_class_seconds(self.start, self.end)
        self.assertEqual(_attendance_status(40 * 60, base, 75), "absent")
