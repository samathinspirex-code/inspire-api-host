import unittest
from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path

from app.modules.lms.zoom_service import _claim_host, _recording_api_ref, _recording_download_token, _zak_token_url, delete_class_recording


class _Mappings:
    def all(self):
        return [{"connection_id": 1, "concurrent": 0, "capacity": 1}]


class _Result:
    def mappings(self):
        return _Mappings()


class _Database:
    sql = ""
    parameters = {}

    async def execute(self, statement, parameters):
        self.sql = str(statement)
        self.parameters = parameters
        return _Result()


class ZoomHostAllocationTests(unittest.IsolatedAsyncioTestCase):
    def test_zak_token_request_targets_the_connected_zoom_user(self):
        self.assertEqual(
            _zak_token_url({"zoom_user_id": "host/user@example.com"}),
            "https://api.zoom.us/v2/users/host%2Fuser%40example.com/token",
        )

    def test_recording_webhook_download_uses_its_download_token(self):
        self.assertEqual(
            _recording_download_token({"download_token": "webhook-token"}, "oauth-token"),
            "webhook-token",
        )

    def test_recording_download_falls_back_to_oauth_token(self):
        self.assertEqual(_recording_download_token({}, "oauth-token"), "oauth-token")

    def test_recording_uuid_is_double_encoded_for_refresh(self):
        self.assertEqual(_recording_api_ref("/abc=="), "%252Fabc%253D%253D")

    def test_zoom_migration_moves_recordings_out_of_template_modules(self):
        migration = (Path(__file__).parents[1] / "app/modules/lms/sql/zoom_integration.sql").read_text()
        self.assertIn("ALTER TABLE lms_zoom_recordings ADD COLUMN IF NOT EXISTS resource_url", migration)
        self.assertIn("DELETE FROM lms_modules module WHERE module.title LIKE 'Recordings", migration)

    def test_recording_is_hidden_before_best_effort_vimeo_cleanup(self):
        source = inspect.getsource(delete_class_recording)
        self.assertLess(source.index("SET status='deleted'"), source.index("vimeo.delete_video"))

    async def test_new_meeting_gives_nullable_exclusion_parameter_a_bigint_type(self):
        database = _Database()
        start = datetime(2026, 9, 15, 6, 29, tzinfo=timezone.utc)

        host = await _claim_host(database, 7, start, start + timedelta(hours=6))

        self.assertEqual(host["connection_id"], 1)
        self.assertIsNone(database.parameters["exclude_id"])
        self.assertIn("CAST(:exclude_id AS BIGINT)", database.sql)


if __name__ == "__main__":
    unittest.main()
