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
        assignment = SimpleNamespace(due_at=now, submission_type="written", status="published", allow_late=False)
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
        assignment = SimpleNamespace(submission_type="written", grades_released=False, due_at=now, allow_late=False)
        submission = SimpleNamespace(status="submitted", expires_at=None)
        repo = MagicMock()
        repo.get_submission = AsyncMock(return_value=submission)
        db = SimpleNamespace(commit=AsyncMock())
        with (patch.object(coursework_service, "_student_context", new=AsyncMock(return_value=(assignment,))),
              patch.object(coursework_service, "CourseworkRepository", return_value=repo)):
            with self.assertRaises(ValidationError):
                await coursework_service.withdraw_submission(db, 4, 2)
        db.commit.assert_not_awaited()

    async def test_late_allowed_keeps_draft_started_before_deadline_open(self):
        now = datetime(2026, 11, 9, 13, tzinfo=timezone.utc)
        assignment = SimpleNamespace(due_at=now - timedelta(minutes=1), submission_type="written",
                                     status="published", allow_late=True)
        draft = SimpleNamespace(status="in_progress", started_at=now - timedelta(days=3), expires_at=None,
                                answer_text=None, attachment_asset_id=None, submitted_at=None)
        result = MagicMock()
        result.all.return_value = [(draft, assignment)]
        db = SimpleNamespace(execute=AsyncMock(return_value=result), commit=AsyncMock())

        count = await coursework_service.finalize_due_drafts(db, now)

        self.assertEqual(count, 0)
        self.assertEqual(draft.status, "in_progress")
        db.commit.assert_not_awaited()

    async def test_late_allowed_lets_early_starter_submit_after_deadline(self):
        now = datetime.now(timezone.utc)
        assignment = SimpleNamespace(submission_type="written", due_at=now - timedelta(hours=1), allow_late=True)
        submission = SimpleNamespace(status="in_progress", started_at=now - timedelta(days=2), expires_at=None)
        repo = MagicMock()
        repo.get_submission = AsyncMock(return_value=submission)
        with (patch.object(coursework_service, "_student_context", new=AsyncMock(return_value=(assignment,))),
              patch.object(coursework_service, "CourseworkRepository", return_value=repo)):
            _, returned = await coursework_service._active_submission(SimpleNamespace(commit=AsyncMock()), 4, 2)
        self.assertIs(returned, submission)
        self.assertEqual(submission.status, "in_progress")

    async def test_late_not_allowed_closes_draft_at_deadline(self):
        now = datetime.now(timezone.utc)
        due = now - timedelta(minutes=5)
        assignment = SimpleNamespace(submission_type="written", due_at=due, allow_late=False)
        submission = SimpleNamespace(status="in_progress", started_at=now - timedelta(days=2), expires_at=None,
                                     answer_text=None, attachment_asset_id=31, submitted_at=None)
        repo = MagicMock()
        repo.get_submission = AsyncMock(return_value=submission)
        db = SimpleNamespace(commit=AsyncMock())
        with (patch.object(coursework_service, "_student_context", new=AsyncMock(return_value=(assignment,))),
              patch.object(coursework_service, "CourseworkRepository", return_value=repo)):
            with self.assertRaises(ValidationError):
                await coursework_service._active_submission(db, 4, 2)
        self.assertEqual(submission.status, "submitted")
        self.assertEqual(submission.submitted_at, due)


class ReopenAfterEditTests(unittest.IsolatedAsyncioTestCase):
    def assignment(self, **changes):
        now = datetime.now(timezone.utc)
        values = dict(assignment_id=4, status="published", submission_type="written", assignment_type="regular",
                      grades_released=False, due_at=now - timedelta(hours=1), allow_late=True)
        values.update(changes)
        return SimpleNamespace(**values)

    def db_with(self, rows):
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        return SimpleNamespace(execute=AsyncMock(return_value=result))

    async def test_reopens_auto_zero_and_auto_submitted_draft_but_not_student_or_marked_work(self):
        old_due = datetime(2026, 11, 9, 12, 29, tzinfo=timezone.utc)
        auto_zero = SimpleNamespace(status="expired", feedback=coursework_service.AUTO_ZERO_FEEDBACK, marked_by=None,
                                    submitted_at=old_due, expires_at=None, marks_awarded=Decimal("0"),
                                    grade_band="fail", marked_at=old_due, started_at=old_due)
        auto_draft = SimpleNamespace(status="submitted", feedback=None, marked_by=None, submitted_at=old_due,
                                     expires_at=None, marks_awarded=None, grade_band=None, marked_at=None,
                                     started_at=old_due - timedelta(days=1))
        handed_in = SimpleNamespace(status="submitted", feedback=None, marked_by=None,
                                    submitted_at=old_due - timedelta(hours=2), expires_at=None)
        marked = SimpleNamespace(status="expired", feedback=coursework_service.AUTO_ZERO_FEEDBACK, marked_by=9,
                                 submitted_at=old_due, expires_at=None)
        db = self.db_with([auto_zero, auto_draft, handed_in, marked])

        count = await coursework_service.reopen_closed_submissions(db, self.assignment(), old_due)

        self.assertEqual(count, 2)
        for row in (auto_zero, auto_draft):
            self.assertEqual(row.status, "in_progress")
            self.assertIsNone(row.marks_awarded)
            self.assertIsNone(row.feedback)
            self.assertIsNone(row.submitted_at)
        self.assertEqual(handed_in.status, "submitted")
        self.assertEqual(marked.status, "expired")

    async def test_nothing_reopens_while_deadline_passed_and_late_not_allowed(self):
        db = self.db_with([])
        count = await coursework_service.reopen_closed_submissions(db, self.assignment(allow_late=False), None)
        self.assertEqual(count, 0)
        db.execute.assert_not_awaited()

    async def test_extended_deadline_reopens_without_late_submissions(self):
        now = datetime.now(timezone.utc)
        old_due = now - timedelta(hours=1)
        auto_zero = SimpleNamespace(status="expired", feedback=coursework_service.AUTO_ZERO_FEEDBACK, marked_by=None,
                                    submitted_at=old_due, expires_at=None)
        db = self.db_with([auto_zero])
        count = await coursework_service.reopen_closed_submissions(
            db, self.assignment(allow_late=False, due_at=now + timedelta(days=2)), old_due)
        self.assertEqual(count, 1)
        self.assertEqual(auto_zero.status, "in_progress")

    async def test_released_grades_are_never_reopened(self):
        db = self.db_with([])
        count = await coursework_service.reopen_closed_submissions(db, self.assignment(grades_released=True), None)
        self.assertEqual(count, 0)
        db.execute.assert_not_awaited()


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
