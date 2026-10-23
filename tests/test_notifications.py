import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from app.modules.lms.notification_email import build_notification_payload
from app.modules.lms.notification_service import _audience_label, local_time, reminder_due
from app.modules.lms.schemas import AnnouncementCreate


class NotificationTests(unittest.TestCase):
    def test_reminder_is_due_only_inside_delivery_window(self):
        event = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
        self.assertFalse(reminder_due(event, 60, event - timedelta(minutes=61)))
        self.assertTrue(reminder_due(event, 60, event - timedelta(minutes=60)))
        self.assertTrue(reminder_due(event, 60, event - timedelta(minutes=51)))
        self.assertFalse(reminder_due(event, 60, event - timedelta(minutes=49)))

    def test_colombo_time_is_used_in_messages(self):
        value = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
        self.assertIn("03:30 PM", local_time(value))

    def test_student_timezone_handles_date_rollover_and_daylight_saving(self):
        winter = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
        summer = datetime(2026, 7, 1, 0, 30, tzinfo=timezone.utc)
        self.assertIn("31 December 2025 at 07:30 PM", local_time(winter, "America/New_York"))
        self.assertIn("30 June 2026 at 08:30 PM", local_time(summer, "America/New_York"))

    def test_mailjet_payload_escapes_notification_content(self):
        payload = build_notification_payload(
            "student@example.com", "<Student>", "Class <updated>",
            "Click <script>alert(1)</script>", "https://lms.example.com/?a=1&b=2", "event-1",
        )
        message = payload["Messages"][0]
        self.assertNotIn("<script>", message["HTMLPart"])
        self.assertIn("&lt;script&gt;", message["HTMLPart"])
        self.assertEqual(message["TrackOpens"], "disabled")
        self.assertEqual(message["CustomID"], "event-1")

    def test_online_class_email_uses_branded_meeting_template(self):
        payload = build_notification_payload(
            "student@example.com",
            "Samath",
            "Online class scheduled: Market Research",
            "FT-B-L3-M5 · Sep_2026 — 25 September 2026 at 11:19 AM.",
            "https://lms.example.com/?view=meetings",
            "meeting-1",
            notification_type="class_schedule",
        )
        message = payload["Messages"][0]
        self.assertEqual(message["Subject"], "Online class scheduled · Market Research")
        self.assertIn("INSPIRE", message["HTMLPart"])
        self.assertIn("Class details", message["HTMLPart"])
        self.assertIn("View online class", message["HTMLPart"])
        self.assertIn("?view=meetings", message["HTMLPart"])

    def test_cancelled_class_email_uses_cancelled_action(self):
        payload = build_notification_payload(
            "student@example.com", "Samath", "Online class cancelled: Market Research",
            "This class will no longer take place.", None, "meeting-2",
            notification_type="class_schedule",
        )
        message = payload["Messages"][0]
        self.assertIn("View class update", message["HTMLPart"])
        self.assertIn("#B42318", message["HTMLPart"])

    def test_assignment_summary_email_uses_safe_lecturer_template(self):
        payload = build_notification_payload(
            "lecturer@example.com", "Lecturer", "Submission summary: Essay",
            "Submitted (1): Alice\nNot submitted (1): <Bob>",
            "https://lms.example.com/?view=assignments", "assignment-4",
            notification_type="assignment_deadline_summary",
        )
        message = payload["Messages"][0]
        self.assertIn("Deadline summary", message["HTMLPart"])
        self.assertIn("Open assignments", message["HTMLPart"])
        self.assertIn("&lt;Bob&gt;", message["HTMLPart"])
        self.assertNotIn("<Bob>", message["HTMLPart"])

    def test_admin_audiences_do_not_require_course_or_class_ids(self):
        common = {
            "title": "Staff notice", "message": "Review the latest LMS update.",
            "publish_at": datetime.now(timezone.utc), "status": "published",
        }
        for audience_type in ("admin", "super_admin"):
            payload = AnnouncementCreate(audience_type=audience_type, **common)
            self.assertIsNone(payload.audience_id)
        with self.assertRaises(ValidationError):
            AnnouncementCreate(audience_type="course", **common)

    def test_admin_audience_labels_are_clear(self):
        self.assertEqual(asyncio.run(_audience_label(None, "admin", None)), "LMS Admins")
        self.assertEqual(asyncio.run(_audience_label(None, "super_admin", None)), "LMS Super Admins")


if __name__ == "__main__":
    unittest.main()
