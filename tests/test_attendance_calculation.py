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
