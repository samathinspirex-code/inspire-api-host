import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.errors import ValidationError
from app.modules.auth.invitation_email import EmailDeliveryResult
from app.modules.lms.notification_service import (
    NOTIFICATION_CATEGORIES,
    deliver_pending_emails,
    get_notification_preferences,
    notification_category,
    update_notification_preferences,
)
from app.modules.lms.schemas.notification import NotificationPreferencesUpdate


def _scalars(items):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: items))


class NotificationPreferenceTests(unittest.TestCase):
    def test_every_type_the_lms_sends_has_a_category(self):
        sent_types = {
            "announcement", "class_schedule", "class_reminder", "grade_released", "resubmission_opened",
            "assignment_published", "exam_published", "practice_test_published", "assignment_deadline",
            "exam_deadline", "practice_test_deadline", "assignment_deadline_summary",
            "assignment_release_reminder", "simulator",
        }
        self.assertEqual({t for t in sent_types if notification_category(t) is None}, set())
        self.assertEqual(notification_category("practice_test_deadline"), "practice_tests")

    def test_roles_see_only_relevant_categories_and_default_on(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=_scalars([])))
        student = asyncio.run(get_notification_preferences(db, 7, "STUDENT"))
        admin = asyncio.run(get_notification_preferences(db, 1, "ADMIN"))
        lecturer = asyncio.run(get_notification_preferences(db, 2, "LECTURER"))
        self.assertEqual([item.category for item in student.data], [item["key"] for item in NOTIFICATION_CATEGORIES])
        self.assertEqual([item.category for item in admin.data], ["classes", "announcements"])
        self.assertIn("assignments", [item.category for item in lecturer.data])
        self.assertTrue(all(item.in_app_enabled and item.email_enabled for item in student.data))

    def test_saved_switches_are_returned(self):
        saved = SimpleNamespace(category="practice_tests", in_app_enabled=True, email_enabled=False)
        db = SimpleNamespace(execute=AsyncMock(return_value=_scalars([saved])))
        result = asyncio.run(get_notification_preferences(db, 7, "STUDENT"))
        practice = next(item for item in result.data if item.category == "practice_tests")
        self.assertEqual((practice.in_app_enabled, practice.email_enabled), (True, False))

    def test_update_rejects_categories_outside_the_role(self):
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        payload = NotificationPreferencesUpdate(data=[{"category": "grades", "in_app_enabled": True, "email_enabled": False}])
        with self.assertRaises(ValidationError):
            asyncio.run(update_notification_preferences(db, payload, 1, "ADMIN"))
        db.execute.assert_not_awaited()

    def test_switched_off_email_category_is_not_sent(self):
        item = SimpleNamespace(user_id=7, event_key="announcement:5", notification_type="announcement",
            title="Holiday", message="Closed on Monday", action_url=None, email_attempts=0,
            email_status="pending", email_sent_at=None, email_provider_id=None, email_error=None)
        user = SimpleNamespace(email="student@example.com", full_name="Student")
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            SimpleNamespace(all=lambda: [(item, user)]),
            SimpleNamespace(all=lambda: [(7, "announcements")]),
        ]), commit=AsyncMock())
        with patch("app.modules.lms.notification_service.send_notification_email", new_callable=AsyncMock,
                   return_value=EmailDeliveryResult(True)) as send_email:
            sent, failed = asyncio.run(deliver_pending_emails(db, now=datetime(2026, 11, 11, tzinfo=timezone.utc)))
        send_email.assert_not_awaited()
        self.assertEqual((sent, failed), (0, 0))
        self.assertEqual((item.email_status, item.email_attempts), ("disabled", 0))


if __name__ == "__main__":
    unittest.main()
