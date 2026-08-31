import unittest
from datetime import datetime, timedelta, timezone

from app.modules.lms.notification_email import build_notification_payload
from app.modules.lms.notification_service import local_time, reminder_due


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


if __name__ == "__main__":
    unittest.main()
