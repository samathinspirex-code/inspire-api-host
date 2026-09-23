import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.modules.lms import vimeo_service


class FakeDatabase:
    def __init__(self):
        self.events = []
        self.item = SimpleNamespace(
            item_type="video",
            resource_url="https://vimeo.com/12345",
            thumbnail_url="https://images.example/old.jpg",
        )

    async def get(self, _model, _item_id):
        self.events.append("db_get")
        return self.item

    async def rollback(self):
        self.events.append("db_release")

    async def commit(self):
        self.events.append("db_commit")


class FakeVimeoClient:
    events = []

    async def __aenter__(self):
        self.events.append("vimeo_enter")
        return self

    async def __aexit__(self, *_args):
        self.events.append("vimeo_exit")

    async def get_video(self, _video_uri):
        self.events.append("vimeo_get")
        return {"pictures": {"sizes": [{"width": 640, "link": "https://images.example/final.jpg"}]}}


class VimeoConnectionSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_connection_is_released_before_vimeo_network_call(self):
        db = FakeDatabase()
        FakeVimeoClient.events = db.events
        with patch.object(vimeo_service, "VimeoClient", FakeVimeoClient):
            refreshed = await vimeo_service.refresh_learning_item_thumbnail(db, 100)

        self.assertTrue(refreshed)
        self.assertLess(db.events.index("db_release"), db.events.index("vimeo_get"))
        self.assertEqual(db.item.thumbnail_url, "https://images.example/final.jpg")
        self.assertEqual(db.events[-1], "db_commit")


if __name__ == "__main__":
    unittest.main()
