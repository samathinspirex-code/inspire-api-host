import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.core.errors import ForbiddenError
from app.modules.lms import notification_service
from app.modules.lms.notification_service import _audience_label, _audience_user_ids, _can_manage_audience
from app.modules.lms.schemas import AnnouncementCreate

COMMON = {"title": "Holiday", "message": "No classes on Monday", "publish_at": datetime.now(timezone.utc)}


class AnnouncementAudienceTests(unittest.TestCase):
    def test_several_classes_need_two_distinct_ids(self):
        AnnouncementCreate(audience_type="classes", audience_class_ids=[3, 4], **COMMON)
        for ids in ([3], [3, 3], []):
            with self.assertRaises(ValidationError):
                AnnouncementCreate(audience_type="classes", audience_class_ids=ids, **COMMON)

    def test_study_mode_audiences_take_no_ids(self):
        AnnouncementCreate(audience_type="part_time", **COMMON)
        AnnouncementCreate(audience_type="full_time", **COMMON)
        with self.assertRaises(ValidationError):
            AnnouncementCreate(audience_type="part_time", audience_id=3, **COMMON)
        with self.assertRaises(ValidationError):
            AnnouncementCreate(audience_type="full_time", audience_class_ids=[3, 4], **COMMON)

    def test_study_mode_labels(self):
        self.assertEqual(asyncio.run(_audience_label(None, "part_time", None)), "All part-time classes")
        self.assertEqual(asyncio.run(_audience_label(None, "full_time", None)), "All full-time classes")

    def test_lecturers_cannot_announce_to_every_part_time_class(self):
        with self.assertRaises(ForbiddenError):
            asyncio.run(_can_manage_audience(None, "LECTURER", 9, "part_time", None))

    def test_recipients_are_merged_across_classes_without_duplicates(self):
        per_class = {3: [10, 11, 50], 4: [11, 12, 50]}
        original = notification_service._audience_user_ids

        async def fake(db, audience_type, audience_id, class_ids=None):
            if audience_type == "class":
                return per_class[audience_id]
            return await original(db, audience_type, audience_id, class_ids)

        with patch.object(notification_service, "_audience_user_ids", new=fake):
            users = asyncio.run(fake(None, "classes", None, [3, 4]))
        self.assertEqual(sorted(users), [10, 11, 12, 50])

    def test_study_mode_recipients_use_matching_classes(self):
        original = notification_service._audience_user_ids

        async def fake(db, audience_type, audience_id, class_ids=None):
            if audience_type == "class":
                return {3: [10], 4: [11]}[audience_id]
            return await original(db, audience_type, audience_id, class_ids)

        with patch.object(notification_service, "_audience_user_ids", new=fake), \
             patch.object(notification_service, "_study_mode_class_ids", new=AsyncMock(return_value=[3, 4])) as modes:
            users = asyncio.run(fake(None, "part_time", None))
        modes.assert_awaited_once_with(None, "part_time")
        self.assertEqual(sorted(users), [10, 11])


if __name__ == "__main__":
    unittest.main()
