"""Activity log descriptions and retention without touching the application database."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from app.core.activity_audit import _describe_change
from app.modules.cms.activity_service import list_activity_log


class ActivityDescriptionTests(unittest.TestCase):
    def test_student_actions_are_not_described_as_administrative_creates(self):
        self.assertEqual(
            _describe_change("POST", "/api/v1/lms/meetings/101/zoom/join"),
            ("Requested meeting join", "Online Meetings", False),
        )
        self.assertEqual(
            _describe_change("POST", "/api/v1/lms/exams/26/start"),
            ("Started exam", "Exams", False),
        )
        self.assertEqual(
            _describe_change("PATCH", "/api/v1/lms/profile"),
            ("Saved own profile", "Profile", False),
        )
        self.assertEqual(
            _describe_change("POST", "/api/v1/lms/profile/media/261/complete"),
            ("Updated profile photo", "Profile", False),
        )
        self.assertEqual(
            _describe_change("POST", "/api/v1/lms/meetings"),
            ("Created online meeting", "Online Meetings", False),
        )


class ActivityListTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_label_is_corrected_and_cards_use_retention_window(self):
        old_row = SimpleNamespace(
            activity_log_id=4073, actor_name="Student", actor_email="student@example.test",
            action="Created online meeting", module="Online Meetings",
            request_method="POST", request_path="/api/v1/lms/meetings/101/zoom/join",
            result="Success", is_sensitive=False,
            occurred_at=datetime(2026, 10, 7, 16, 11, tzinfo=timezone.utc),
        )
        results = [
            MagicMock(),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [old_row])),
            SimpleNamespace(scalar_one=lambda: 1),
            SimpleNamespace(one=lambda: (12, 3, 2)),
        ]
        db = SimpleNamespace(execute=AsyncMock(side_effect=results), commit=AsyncMock())

        report = await list_activity_log(db, None, 100)

        self.assertEqual(report.data[0].action, "Requested meeting join")
        self.assertEqual(report.data[0].target, "Meeting #101")
        self.assertEqual(report.metrics.events_last_7_days, 12)
        self.assertEqual(report.metrics.user_changes_last_7_days, 3)
        self.assertEqual(report.metrics.sensitive_actions_last_7_days, 2)
        self.assertEqual(db.commit.await_count, 1)
        statements = [str(call.args[0].compile(dialect=postgresql.dialect())) for call in db.execute.await_args_list]
        self.assertIn("DELETE FROM cms_activity_log", statements[0])
        self.assertIn("occurred_at < now() -", statements[0])
        self.assertIn("occurred_at >= now() -", statements[1])
        self.assertIn("occurred_at >= now() -", statements[3])
