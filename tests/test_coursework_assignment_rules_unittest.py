import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pydantic import ValidationError as PydanticValidationError

from app.modules.lms.coursework_service import (
    _expire_with_zero,
    assignment_is_available,
    fixed_expiry,
    remaining_seconds,
)
from app.modules.lms.models import LmsCourseworkSubmission
from app.modules.lms.schemas.coursework import CourseworkAssignmentCreate


def payload(**changes):
    values = {
        "course_id": 10,
        "target_type": "course",
        "target_id": 10,
        "title": "QA assignment",
        "instructions": "Complete every question.",
        "assignment_type": "regular",
        "submission_type": "written",
        "max_marks": 100,
        "allow_late": False,
        "status": "draft",
    }
    values.update(changes)
    return values


class CourseworkAssignmentRulesTest(unittest.TestCase):
    def test_future_assignment_is_hidden_until_available(self):
        now = datetime.now(timezone.utc)
        self.assertFalse(assignment_is_available(now + timedelta(minutes=1), now))
        self.assertTrue(assignment_is_available(now, now))
        self.assertTrue(assignment_is_available(None, now))

    def test_due_time_must_follow_available_time(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(PydanticValidationError):
            CourseworkAssignmentCreate(**payload(available_from=now, due_at=now - timedelta(seconds=1)))

    def test_timed_assignment_requires_duration_and_disallows_late_work(self):
        with self.assertRaises(PydanticValidationError):
            CourseworkAssignmentCreate(**payload(assignment_type="timed", duration_minutes=None))
        with self.assertRaises(PydanticValidationError):
            CourseworkAssignmentCreate(**payload(assignment_type="timed", duration_minutes=30, allow_late=True))

    def test_pdf_assignment_requires_question_paper(self):
        with self.assertRaises(PydanticValidationError):
            CourseworkAssignmentCreate(**payload(submission_type="pdf_annotation"))
        item = CourseworkAssignmentCreate(**payload(submission_type="pdf_annotation", question_paper_asset_id=22))
        self.assertEqual(item.question_paper_asset_id, 22)

    def test_expired_submission_is_automatically_graded_zero(self):
        now = datetime.now(timezone.utc)
        submission = LmsCourseworkSubmission(
            assignment_id=1,
            student_user_id=2,
            status="in_progress",
            started_at=now - timedelta(hours=1),
        )
        _expire_with_zero(submission, now)
        self.assertEqual(submission.status, "expired")
        self.assertEqual(submission.marks_awarded, Decimal("0"))
        self.assertIsNotNone(submission.marked_at)

    def test_timed_expiry_never_exceeds_due_date(self):
        now = datetime.now(timezone.utc)
        due = now + timedelta(minutes=20)
        expiry = fixed_expiry(now, 60, due)
        self.assertEqual(expiry, due)
        self.assertEqual(remaining_seconds(expiry, now), 1200)


if __name__ == "__main__":
    unittest.main()
