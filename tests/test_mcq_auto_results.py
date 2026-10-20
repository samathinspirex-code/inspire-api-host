import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.errors import ValidationError
from app.modules.lms import exam_service, gradebook_service


class McqAutoResultsTests(unittest.IsolatedAsyncioTestCase):
    async def test_finished_mcq_stores_score_for_private_release(self):
        question = SimpleNamespace(
            question_id=12, question_type="mcq", correct_option_index=1,
            marks=Decimal("2"),
        )
        answer = SimpleNamespace(
            question_id=12, selected_option_index=1, answer_text=None,
            is_correct=None, auto_marks=None,
        )
        mirror = SimpleNamespace(status="in_progress", marks_awarded=None, marked_at=None)
        attempt = SimpleNamespace(
            attempt_id=4, student_user_id=8, status="in_progress",
            expires_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        )
        exam = SimpleNamespace(exam_id=3, assessment_kind="graded_mcq")
        assignment = SimpleNamespace(assignment_id=2)
        db = MagicMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [answer])),
            SimpleNamespace(scalar_one=lambda: mirror),
        ])

        with patch.object(exam_service, "_question_rows", new=AsyncMock(return_value=[question])):
            await exam_service._finalize_attempt(db, exam, assignment, attempt)

        self.assertEqual(attempt.status, "reviewed")
        self.assertEqual(attempt.total_marks, Decimal("2"))
        self.assertEqual(mirror.status, "reviewed")
        self.assertEqual(mirror.marks_awarded, Decimal("2"))
        self.assertIsNone(attempt.grade_band)
        self.assertIsNone(mirror.grade_band)
        self.assertTrue(answer.is_correct)
        db.commit.assert_awaited_once()

    async def test_release_backfills_previously_scored_mcq_attempts(self):
        assignment = SimpleNamespace(assignment_id=2, course_id=3, submission_type="mcq", grades_released=False)
        submission = SimpleNamespace(student_user_id=8, status="submitted", marks_awarded=None, marked_at=None)
        submitted_at = datetime(2026, 10, 20, tzinfo=timezone.utc)
        attempt = SimpleNamespace(
            student_user_id=8, status="submitted", auto_marks=Decimal("7"),
            total_marks=None, submitted_at=submitted_at, marked_at=None,
        )
        linked_exam = SimpleNamespace(grades_released=False)
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [submission])),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [attempt])),
            SimpleNamespace(scalar_one_or_none=lambda: linked_exam),
        ])
        db.commit = AsyncMock()
        repo = MagicMock()
        repo.get_assignment = AsyncMock(return_value=assignment)
        repo.get_assignment_context = AsyncMock(return_value="context")
        with patch.object(gradebook_service, "CourseworkRepository", return_value=repo), \
             patch.object(gradebook_service, "_ensure_lecturer_course", new=AsyncMock()), \
             patch.object(gradebook_service, "_assignment_item", return_value="released"), \
             patch.object(gradebook_service.notification_service, "notify_grade_release", new=AsyncMock()):
            result = await gradebook_service.set_grade_release(db, 2, True, 9)

        self.assertEqual(result, "released")
        self.assertEqual(submission.marks_awarded, Decimal("7"))
        self.assertEqual(attempt.total_marks, Decimal("7"))
        self.assertIsNone(submission.grade_band)
        self.assertIsNone(attempt.grade_band)
        self.assertTrue(assignment.grades_released)
        self.assertTrue(linked_exam.grades_released)

    async def test_timer_expiry_returns_the_finalized_attempt(self):
        attempt = SimpleNamespace(attempt_id=4, exam_id=3, student_user_id=8, status="reviewed")
        exam = SimpleNamespace(exam_id=3)
        db = MagicMock()
        db.get = AsyncMock(side_effect=[attempt, exam])
        with patch.object(exam_service, "save_answers", new=AsyncMock(side_effect=ValidationError(
            "The exam timer has expired and your answers were submitted"
        ))), patch.object(exam_service, "_attempt_response", new=AsyncMock(return_value="finished")):
            result = await exam_service.submit_exam(db, 4, SimpleNamespace(answers=[]), 8)
        self.assertEqual(result, "finished")


if __name__ == "__main__":
    unittest.main()
