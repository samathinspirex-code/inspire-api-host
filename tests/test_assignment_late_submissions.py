import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.errors import ValidationError
from app.modules.lms import exam_service
from app.modules.lms.coursework_service import submission_is_late
from app.modules.lms.schemas.coursework import CourseworkAssignmentCreate
from app.modules.lms.schemas.template_bank import TemplateApplyRequest


class LateSubmissionRulesTests(unittest.TestCase):
    def test_mcq_can_enable_late_submissions_and_templates_keep_option(self):
        payload = CourseworkAssignmentCreate(course_id=1, target_id=1, title="Quiz", instructions="Answer all questions", submission_type="mcq", duration_minutes=20, allow_late=True)
        self.assertTrue(payload.allow_late)
        self.assertEqual(payload.assignment_type, "timed")
        self.assertTrue(TemplateApplyRequest(course_id=1, allow_late=True).allow_late)

    def test_late_timer_is_full_duration_and_normal_timer_still_capped(self):
        start = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)
        due = start + timedelta(minutes=5)
        self.assertEqual(exam_service.fixed_exam_expiry(start, 20, due), due)
        self.assertEqual(exam_service.fixed_exam_expiry(start, 20, due, allow_late=True), start + timedelta(minutes=20))

    def test_late_status_uses_actual_submission_time_and_handles_naive_utc(self):
        due = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)
        for status in ("submitted", "reviewed", "returned"):
            with self.subTest(status=status):
                self.assertTrue(submission_is_late(SimpleNamespace(status=status, submitted_at=(due + timedelta(seconds=1)).replace(tzinfo=None)), due))
                self.assertFalse(submission_is_late(SimpleNamespace(status=status, submitted_at=due), due))
        self.assertFalse(submission_is_late(SimpleNamespace(status="expired", submitted_at=due + timedelta(seconds=1)), due))
        self.assertFalse(submission_is_late(SimpleNamespace(status="in_progress", submitted_at=None), due))


class LateMcqStartTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, allow_late):
        now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        exam = SimpleNamespace(exam_id=1, assessment_kind="graded_mcq", available_from=None,
                               due_at=now - timedelta(hours=1), duration_minutes=20,
                               randomize_questions=False, randomize_options=False)
        assignment = SimpleNamespace(assignment_id=2, allow_late=allow_late)
        question = SimpleNamespace(question_id=3, question_type="mcq", options=["A", "B"])
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db = SimpleNamespace(add=MagicMock(), flush=AsyncMock(), execute=AsyncMock(return_value=result), commit=AsyncMock(), refresh=AsyncMock())
        with (patch.object(exam_service, "_student_exam_context", new=AsyncMock(return_value=(exam, assignment))),
              patch.object(exam_service, "_get_attempt", new=AsyncMock(return_value=None)),
              patch.object(exam_service, "_question_rows", new=AsyncMock(return_value=[question])),
              patch.object(exam_service, "utc_now", return_value=now),
              patch("app.modules.lms.coursework_service.utc_now", return_value=now),
              patch.object(exam_service, "_attempt_response", new=AsyncMock(side_effect=lambda db, exam, attempt: attempt))):
            return await exam_service.start_exam(db, 1, 7), now

    async def test_late_start_allowed_with_full_timer(self):
        attempt, now = await self.start(True)
        self.assertEqual(attempt.expires_at, now + timedelta(minutes=20))

    async def test_late_start_rejected_when_option_disabled(self):
        with self.assertRaises(ValidationError):
            await self.start(False)
