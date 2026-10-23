import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.lms import notification_service


class AssignmentReleaseNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_future_available_from_delays_student_notification(self):
        now = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
        release = now + timedelta(days=2)
        assignment = SimpleNamespace(
            assignment_id=52, target_type="course", target_id=8,
            title="Quiz 1", available_from=release, due_at=release + timedelta(days=1),
        )
        db = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(notification_service, "utc_now", return_value=now),
            patch.object(notification_service, "_student_audience_user_ids", new=AsyncMock(return_value=[10])),
            patch.object(notification_service, "_enqueue", new=AsyncMock(return_value=1)) as enqueue,
        ):
            await notification_service.notify_assessment_published(db, assignment, "assignment")

        self.assertEqual(enqueue.await_args.args[8], release)
        self.assertTrue(enqueue.await_args.kwargs["update_existing"])
        db.commit.assert_awaited_once()

    async def test_unscheduled_assignment_notifies_when_published(self):
        now = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
        assignment = SimpleNamespace(
            assignment_id=53, target_type="course", target_id=8,
            title="Essay", available_from=None, due_at=None,
        )
        db = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(notification_service, "utc_now", return_value=now),
            patch.object(notification_service, "_student_audience_user_ids", new=AsyncMock(return_value=[10])),
            patch.object(notification_service, "_enqueue", new=AsyncMock(return_value=1)) as enqueue,
        ):
            await notification_service.notify_assessment_published(db, assignment, "assignment")

        self.assertEqual(enqueue.await_args.args[8], now)


if __name__ == "__main__":
    unittest.main()
