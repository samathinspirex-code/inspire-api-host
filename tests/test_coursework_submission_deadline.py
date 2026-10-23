import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.errors import ValidationError
from app.modules.lms import coursework_service, notification_service


class SubmissionDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_submits_saved_file_and_expires_empty_draft(self):
        now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        assignment = SimpleNamespace(due_at=now, submission_type="written", status="published")
        saved = SimpleNamespace(status="in_progress", started_at=now - timedelta(hours=1), expires_at=None,
                                answer_text=None, attachment_asset_id=17, submitted_at=None)
        empty = SimpleNamespace(status="in_progress", started_at=now - timedelta(hours=1), expires_at=None,
                                answer_text="  ", attachment_asset_id=None, submitted_at=None)
        result = MagicMock()
        result.all.return_value = [(saved, assignment), (empty, assignment)]
        db = SimpleNamespace(execute=AsyncMock(return_value=result), commit=AsyncMock())

        count = await coursework_service.finalize_due_drafts(db, now)

        self.assertEqual(count, 2)
        self.assertEqual(saved.status, "submitted")
        self.assertEqual(saved.submitted_at, now)
        self.assertEqual(empty.status, "expired")
        self.assertEqual(empty.marks_awarded, Decimal("0"))
        db.commit.assert_awaited_once()

    async def test_student_can_withdraw_before_deadline_without_resetting_timer(self):
        now = datetime.now(timezone.utc)
        assignment = SimpleNamespace(submission_type="written", grades_released=False,
                                     due_at=now + timedelta(hours=1))
        submission = SimpleNamespace(status="submitted", expires_at=now + timedelta(minutes=10),
                                     started_at=now - timedelta(minutes=5), answer_text="answer",
                                     attachment_asset_id=17, submitted_at=now, marks_awarded=Decimal("80"),
                                     grade_band="merit", feedback="Good", marked_by=9, marked_at=now)
        repo = MagicMock()
        repo.get_submission = AsyncMock(return_value=submission)
        db = SimpleNamespace(commit=AsyncMock())
        with (patch.object(coursework_service, "_student_context", new=AsyncMock(return_value=(assignment,))),
              patch.object(coursework_service, "CourseworkRepository", return_value=repo),
              patch.object(coursework_service, "_assignment_item", return_value=submission)):
            await coursework_service.withdraw_submission(db, 4, 2)
        self.assertEqual(submission.status, "withdrawn")
        self.assertIsNone(submission.answer_text)
        self.assertIsNone(submission.attachment_asset_id)
        self.assertIsNone(submission.marks_awarded)
        self.assertEqual(submission.expires_at, now + timedelta(minutes=10))

    async def test_edit_reopens_submission_without_resetting_timer_or_old_grade(self):
        now = datetime.now(timezone.utc)
        assignment = SimpleNamespace(submission_type="written", grades_released=False,
                                     due_at=now + timedelta(hours=1), question_paper_asset_id=None)
        expiry = now + timedelta(minutes=10)
        submission = SimpleNamespace(status="reviewed", expires_at=expiry, answer_text="answer",
                                     attachment_asset_id=None, submitted_at=now, marks_awarded=Decimal("80"),
                                     grade_band="merit", feedback="Good", marked_by=9, marked_at=now)
        repo = MagicMock()
        repo.get_submission = AsyncMock(return_value=submission)
        db = SimpleNamespace(commit=AsyncMock())
        with (patch.object(coursework_service, "_student_context", new=AsyncMock(return_value=(assignment,))),
              patch.object(coursework_service, "CourseworkRepository", return_value=repo),
              patch.object(coursework_service, "_assignment_material", new=AsyncMock(return_value=None)),
              patch.object(coursework_service, "_assignment_item", return_value=submission)):
            await coursework_service.start_assignment(db, 4, 2)
        self.assertEqual(submission.status, "in_progress")
        self.assertEqual(submission.answer_text, "answer")
        self.assertEqual(submission.expires_at, expiry)
        self.assertIsNone(submission.marks_awarded)
        self.assertIsNone(submission.submitted_at)

    async def test_withdrawal_is_rejected_at_deadline(self):
        now = datetime.now(timezone.utc)
        assignment = SimpleNamespace(submission_type="written", grades_released=False, due_at=now)
        submission = SimpleNamespace(status="submitted", expires_at=None)
        repo = MagicMock()
        repo.get_submission = AsyncMock(return_value=submission)
        db = SimpleNamespace(commit=AsyncMock())
        with (patch.object(coursework_service, "_student_context", new=AsyncMock(return_value=(assignment,))),
              patch.object(coursework_service, "CourseworkRepository", return_value=repo)):
            with self.assertRaises(ValidationError):
                await coursework_service.withdraw_submission(db, 4, 2)
        db.commit.assert_not_awaited()


class AssignmentFollowupTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_summary_names_submitters_and_non_submitters(self):
        now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        assignment = SimpleNamespace(assignment_id=4, course_id=5, target_type="class", target_id=6,
                                     title="Essay", due_at=now - timedelta(minutes=1), status="published",
                                     grades_released=False)
        course = SimpleNamespace(code="HND")
        submission = SimpleNamespace(student_user_id=1, status="submitted", submitted_at=now,
                                     marks_awarded=None, marked_at=None)
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[MagicMock(all=MagicMock(return_value=[(assignment, course)])),
                                             MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[submission])))),
                                             MagicMock(all=MagicMock(return_value=[(1, "Alice", "a@example.com"), (2, "Bob", "b@example.com")]))])
        db.get = AsyncMock(return_value=SimpleNamespace(name="Class A"))
        with (patch.object(notification_service, "_assignment_lecturer_ids", new=AsyncMock(return_value=[9])),
              patch.object(notification_service, "_student_audience_user_ids", new=AsyncMock(return_value=[1, 2])),
              patch.object(notification_service, "_enqueue", new=AsyncMock(return_value=1)) as enqueue):
            count = await notification_service.generate_assignment_followups(db, now)
        self.assertEqual(count, 1)
        self.assertEqual(enqueue.await_args.args[3], "assignment_deadline_summary")
        self.assertIn("Submitted (1): Alice", enqueue.await_args.args[5])
        self.assertIn("Not submitted (1): Bob", enqueue.await_args.args[5])

    async def test_release_reminder_waits_two_days_after_last_mark(self):
        now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        assignment = SimpleNamespace(assignment_id=4, course_id=5, target_type="class", target_id=6,
                                     title="Essay", due_at=now - timedelta(days=3), status="published",
                                     grades_released=False)
        course = SimpleNamespace(code="HND")
        submission = SimpleNamespace(status="reviewed", marks_awarded=Decimal("80"),
                                     marked_at=now - timedelta(days=2, minutes=1))
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[MagicMock(all=MagicMock(return_value=[(assignment, course)])),
                                             MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[submission]))))])
        with (patch.object(notification_service, "_assignment_lecturer_ids", new=AsyncMock(return_value=[9])),
              patch.object(notification_service, "_enqueue", new=AsyncMock(return_value=1)) as enqueue):
            count = await notification_service.generate_assignment_followups(db, now)
        self.assertEqual(count, 1)
        self.assertEqual(enqueue.await_args.args[3], "assignment_release_reminder")

    async def test_release_reminder_is_not_sent_after_only_one_day(self):
        now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        assignment = SimpleNamespace(assignment_id=4, course_id=5, target_type="class", target_id=6,
                                     title="Essay", due_at=now - timedelta(days=3), status="published",
                                     grades_released=False)
        submission = SimpleNamespace(status="reviewed", marks_awarded=Decimal("80"),
                                     marked_at=now - timedelta(days=1))
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[MagicMock(all=MagicMock(return_value=[(assignment, SimpleNamespace(code="HND"))])),
                                             MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[submission]))))])
        with (patch.object(notification_service, "_assignment_lecturer_ids", new=AsyncMock(return_value=[9])),
              patch.object(notification_service, "_enqueue", new=AsyncMock(return_value=1)) as enqueue):
            count = await notification_service.generate_assignment_followups(db, now)
        self.assertEqual(count, 0)
        enqueue.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
