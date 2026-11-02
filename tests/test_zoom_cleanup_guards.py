import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.core.errors import ValidationError
from app.modules.lms import zoom_cleanup_service


class _Vimeo:
    def __init__(self, video):
        self.video = video

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get_video(self, _uri):
        return self.video


class ZoomCleanupGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.row = {
            "recording_id": 12, "meeting_id": 7, "class_id": 3,
            "status": "published", "zoom_published_at": datetime.now(timezone.utc),
            "vimeo_video_uri": "/videos/123", "resource_url": "https://vimeo.com/123",
        }

    async def test_processing_recording_is_blocked_before_vimeo_check(self):
        with patch.object(zoom_cleanup_service, "_visible_in_class", new_callable=AsyncMock) as visible:
            with self.assertRaisesRegex(ValidationError, "transferring or processing"):
                await zoom_cleanup_service.ensure_safe_to_delete(None, {**self.row, "status": "processing"})
            visible.assert_not_awaited()

    async def test_recording_missing_from_class_is_blocked(self):
        with patch.object(zoom_cleanup_service, "_visible_in_class", new=AsyncMock(return_value=False)):
            with self.assertRaisesRegex(ValidationError, "not viewable"):
                await zoom_cleanup_service.ensure_safe_to_delete(None, self.row)

    async def test_vimeo_transcode_must_be_complete(self):
        with patch.object(zoom_cleanup_service, "_visible_in_class", new=AsyncMock(return_value=True)):
            with patch.object(zoom_cleanup_service.vimeo_service, "VimeoClient", return_value=_Vimeo({"link": "https://vimeo.com/123", "transcode": {"status": "in_progress"}})):
                with self.assertRaisesRegex(ValidationError, "still processing"):
                    await zoom_cleanup_service.ensure_safe_to_delete(None, self.row)

    async def test_complete_visible_vimeo_recording_is_eligible(self):
        with patch.object(zoom_cleanup_service, "_visible_in_class", new=AsyncMock(return_value=True)):
            with patch.object(zoom_cleanup_service.vimeo_service, "VimeoClient", return_value=_Vimeo({"link": "https://vimeo.com/123", "transcode": {"status": "complete"}})):
                await zoom_cleanup_service.ensure_safe_to_delete(None, self.row)


class ZoomStorageTests(unittest.TestCase):
    def test_storage_units(self):
        self.assertEqual(zoom_cleanup_service._storage_bytes("20 GB"), 20 * 1024 ** 3)
        self.assertEqual(zoom_cleanup_service._storage_bytes("1.5 TB"), int(1.5 * 1024 ** 4))
