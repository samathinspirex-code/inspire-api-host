import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.lms import coursework_service, exam_service


class MissingStudentNumberReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_assignment_results_load_without_student_number(self):
        now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        assignment = SimpleNamespace(assignment_id=52, course_id=53)
        submission = SimpleNamespace(
            submission_id=1, assignment_id=52, student_user_id=8,
            status="submitted", started_at=now, expires_at=now, submitted_at=now,
            answer_text=None, attachment_asset_id=None, marks_awarded=None,
            grade_band=None, feedback=None, marked_at=None,
        )
        user = SimpleNamespace(user_id=8, full_name="Student", email="student@example.test")
        profile = SimpleNamespace(student_number=None)
        attempt = SimpleNamespace(student_user_id=8, status="submitted", auto_marks=Decimal("7"))
        exam = SimpleNamespace(exam_id=31)
        repo = MagicMock()
        repo.get_assignment = AsyncMock(return_value=assignment)
        repo.list_submissions = AsyncMock(return_value=[(submission, user, profile, None)])
        db = MagicMock()
        db.scalar = AsyncMock(return_value=exam)
        db.execute = AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [attempt])
        ))

        with patch.object(coursework_service, "CourseworkRepository", return_value=repo), \
             patch.object(coursework_service, "_ensure_lecturer_course", new=AsyncMock()):
            result = await coursework_service.list_submissions(db, 52, 6)

        self.assertEqual(result.data[0].student_number, "")
        self.assertEqual(result.data[0].auto_marks, Decimal("7"))

    async def test_question_results_load_without_student_number(self):
        now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        exam = SimpleNamespace(exam_id=31, course_id=53)
        assignment = SimpleNamespace(max_marks=Decimal("10"))
        attempt = SimpleNamespace(
            attempt_id=4, student_user_id=8, status="reviewed", question_order=[],
            started_at=now, expires_at=now, submitted_at=now, auto_marks=Decimal("7"),
            manual_marks=Decimal("0"), total_marks=Decimal("7"), feedback=None,
        )
        user = SimpleNamespace(user_id=8, full_name="Student", email="student@example.test")
        profile = SimpleNamespace(student_number=None)
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[
            SimpleNamespace(all=lambda: [(attempt, user, profile)]),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
        ])

        with patch.object(exam_service, "_exam_context", new=AsyncMock(return_value=(exam, assignment))), \
             patch.object(exam_service, "_ensure_lecturer_course", new=AsyncMock()), \
             patch.object(exam_service, "_question_rows", new=AsyncMock(return_value=[])):
            result = await exam_service.list_attempts(db, 31, 6)

        self.assertEqual(result.data[0].student_number, "")
        self.assertEqual(result.data[0].total_marks, Decimal("7"))


if __name__ == "__main__":
    unittest.main()
