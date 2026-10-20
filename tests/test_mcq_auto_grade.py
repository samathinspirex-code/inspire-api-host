import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.errors import ValidationError
from app.modules.lms.coursework_service import grade_band_for_score, mark_submission
from app.modules.lms.exam_service import _finalize_attempt
from app.modules.lms.schemas.coursework import CourseworkMarkUpdate


class McqAutoGradeTest(unittest.IsolatedAsyncioTestCase):
    def test_grade_band_boundaries(self):
        examples = [
            ("0", "fail"), ("49.99", "fail"), ("50", "pass"),
            ("64.99", "pass"), ("65", "merit"),
            ("84.99", "merit"), ("85", "distinction"), ("100", "distinction"),
        ]
        for marks, expected in examples:
            with self.subTest(marks=marks):
                self.assertEqual(grade_band_for_score(Decimal(marks), Decimal("100")), expected)

    async def test_finished_mcq_selects_band_before_release_and_preserves_score(self):
        now = datetime.now(timezone.utc)
        question = SimpleNamespace(
            question_id=1, question_type="mcq", marks=Decimal("7"), correct_option_index=1,
        )
        answer = SimpleNamespace(question_id=1, selected_option_index=1, is_correct=None, auto_marks=None)
        mirror = SimpleNamespace(status="in_progress", marks_awarded=None, grade_band=None)
        attempt = SimpleNamespace(
            attempt_id=2, student_user_id=3, status="in_progress", expires_at=now + timedelta(minutes=20),
            auto_marks=Decimal("0"), total_marks=None, grade_band=None,
        )
        assignment = SimpleNamespace(assignment_id=4, max_marks=Decimal("10"), grades_released=False)
        exam = SimpleNamespace(exam_id=5, assessment_kind="graded_mcq")
        answers_result = MagicMock()
        answers_result.scalars.return_value.all.return_value = [answer]
        submission_result = MagicMock()
        submission_result.scalar_one.return_value = mirror
        db = SimpleNamespace(execute=AsyncMock(side_effect=[answers_result, submission_result]), commit=AsyncMock(), add=MagicMock())

        with patch("app.modules.lms.exam_service._question_rows", new=AsyncMock(return_value=[question])):
            await _finalize_attempt(db, exam, assignment, attempt)

        self.assertEqual(attempt.total_marks, Decimal("7"))
        self.assertEqual(mirror.marks_awarded, Decimal("7"))
        self.assertIsNone(attempt.grade_band)
        self.assertIsNone(mirror.grade_band)
        self.assertFalse(assignment.grades_released)
        db.commit.assert_awaited_once()

    async def test_written_submission_accepts_a_grade_without_entering_marks(self):
        assignment = SimpleNamespace(assignment_id=4, course_id=5, submission_type="written", max_marks=Decimal("100"))
        expected_marks = {"fail": Decimal("0"), "pass": Decimal("50"), "merit": Decimal("65"), "distinction": Decimal("85")}
        for band, marks in expected_marks.items():
            with self.subTest(band=band):
                submission = SimpleNamespace(status="submitted", grade_band=None, marks_awarded=None)
                repository = MagicMock()
                repository.get_submission_context = AsyncMock(return_value=(submission, assignment))
                db = SimpleNamespace(commit=AsyncMock())
                with patch("app.modules.lms.coursework_service.CourseworkRepository", return_value=repository), \
                     patch("app.modules.lms.coursework_service._ensure_lecturer_course", new=AsyncMock()), \
                     patch("app.modules.lms.coursework_service.list_submissions", new=AsyncMock(return_value=None)):
                    await mark_submission(db, 7, CourseworkMarkUpdate(grade_band=band), 9)
                self.assertEqual(submission.grade_band, band)
                self.assertEqual(submission.marks_awarded, marks)
                self.assertEqual(submission.status, "reviewed")
                db.commit.assert_awaited_once()

    async def test_quiz_score_cannot_be_replaced_with_a_manual_grade(self):
        assignment = SimpleNamespace(assignment_id=4, course_id=5, submission_type="mcq", max_marks=Decimal("10"))
        submission = SimpleNamespace(status="reviewed", marks_awarded=Decimal("7"), grade_band=None)
        repository = MagicMock()
        repository.get_submission_context = AsyncMock(return_value=(submission, assignment))
        db = SimpleNamespace(commit=AsyncMock())
        with patch("app.modules.lms.coursework_service.CourseworkRepository", return_value=repository), \
             patch("app.modules.lms.coursework_service._ensure_lecturer_course", new=AsyncMock()):
            with self.assertRaisesRegex(ValidationError, "calculated automatically"):
                await mark_submission(db, 7, CourseworkMarkUpdate(grade_band="merit"), 9)
        self.assertEqual(submission.marks_awarded, Decimal("7"))
        db.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
