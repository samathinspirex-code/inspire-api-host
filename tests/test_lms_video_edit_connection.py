import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.lms import content_service, vimeo_service


class _Vimeo:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def update_video(self, *_args):
        self.events.append("vimeo_update")

    async def get_video(self, *_args):
        self.events.append("vimeo_get")
        return {"pictures": {"sizes": []}}


class VideoEditConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_releases_database_connection_before_vimeo(self):
        events = []
        item = SimpleNamespace(module_id=9, item_type="video", title="Old title", description="Old description",
                               resource_url="https://vimeo.com/123", thumbnail_url=None)
        payload = SimpleNamespace(item_type="video", title="New title", description="New description",
                                  text_content=None, resource_url="https://vimeo.com/123",
                                  model_dump=lambda: {"item_type": "video", "title": "New title", "description": "New description"})
        db = SimpleNamespace(commit=AsyncMock(side_effect=lambda: events.append("db_release")))
        repo = SimpleNamespace(get_item=AsyncMock(return_value=item), update_item=AsyncMock(return_value=item))
        with (
            patch.object(content_service, "ContentRepository", return_value=repo),
            patch.object(content_service, "_ensure_module_manager", new=AsyncMock()),
            patch.object(vimeo_service, "is_configured", return_value=True),
            patch.object(vimeo_service, "VimeoClient", return_value=_Vimeo(events)),
            patch.object(content_service.LearningItemResponse, "model_validate", side_effect=lambda value: value),
        ):
            await content_service.update_learning_item(db, 123, payload, 77)
        self.assertLess(events.index("db_release"), events.index("vimeo_update"))
        repo.update_item.assert_awaited_once()
