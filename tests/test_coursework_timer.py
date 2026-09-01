import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError as PydanticValidationError

from app.modules.lms.coursework_service import fixed_expiry, remaining_seconds
from app.modules.lms.schemas import CourseworkAssignmentCreate


class CourseworkTimerTests(unittest.TestCase):
    def test_fixed_expiry_is_based_on_first_start(self):
        started = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
        expiry = fixed_expiry(started, 60, None)
        self.assertEqual(expiry, started + timedelta(minutes=60))
        self.assertEqual(remaining_seconds(expiry, started + timedelta(minutes=15)), 2700)

    def test_due_date_caps_timed_attempt(self):
        started = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
        due = started + timedelta(minutes=20)
        self.assertEqual(fixed_expiry(started, 60, due), due)

    def test_remaining_time_never_becomes_negative(self):
        expiry = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(remaining_seconds(expiry, expiry + timedelta(seconds=5)), 0)

    def test_timed_assignment_requires_duration(self):
        with self.assertRaises(PydanticValidationError):
            CourseworkAssignmentCreate(
                course_id=1,
                target_id=1,
                title="Timed assessment",
                instructions="Complete every question.",
                assignment_type="timed",
            )

    def test_timed_assignment_cannot_allow_late_submission(self):
        with self.assertRaises(PydanticValidationError):
            CourseworkAssignmentCreate(
                course_id=1,
                target_id=1,
                title="Timed assessment",
                instructions="Complete every question.",
                assignment_type="timed",
                duration_minutes=30,
                allow_late=True,
            )


if __name__ == "__main__":
    unittest.main()
