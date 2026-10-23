import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.lms import vimeo_service


class VimeoDurationRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_finished_video_replaces_placeholder_duration(self):
        item = SimpleNamespace(
            item_type="video", resource_url="https://vimeo.com/123456",
            thumbnail_url="https://vimeo.test/default.jpg", duration_minutes=1,
        )
        db = SimpleNamespace(get=AsyncMock(return_value=item), rollback=AsyncMock(), commit=AsyncMock())
        client = AsyncMock()
        client.get_video.return_value = {
            "duration": 3032,
            "pictures": {"sizes": [{"width": 640, "link": "https://vimeo.test/ready.jpg"}]},
        }
        client.__aenter__.return_value = client
        with patch.object(vimeo_service, "VimeoClient", return_value=client):
            ready = await vimeo_service.refresh_learning_item_thumbnail(db, 5)

        self.assertTrue(ready)
        self.assertEqual(item.duration_minutes, 51)
        self.assertEqual(item.thumbnail_url, "https://vimeo.test/ready.jpg")
        db.commit.assert_awaited_once()

    async def test_processing_video_does_not_get_fake_one_minute_duration(self):
        item = SimpleNamespace(
            item_type="video", resource_url="https://vimeo.com/123456",
            thumbnail_url=None, duration_minutes=None,
        )
        db = SimpleNamespace(get=AsyncMock(return_value=item), rollback=AsyncMock(), commit=AsyncMock())
        client = AsyncMock()
        client.get_video.return_value = {"duration": 0, "pictures": {"sizes": []}}
        client.__aenter__.return_value = client
        with patch.object(vimeo_service, "VimeoClient", return_value=client):
            ready = await vimeo_service.refresh_learning_item_thumbnail(db, 5)

        self.assertFalse(ready)
        self.assertIsNone(item.duration_minutes)
        db.commit.assert_not_awaited()
