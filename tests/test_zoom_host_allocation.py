import unittest
from datetime import datetime, timedelta, timezone

from app.modules.lms.zoom_service import _claim_host, _zak_token_url


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

    async def test_new_meeting_gives_nullable_exclusion_parameter_a_bigint_type(self):
        database = _Database()
        start = datetime(2026, 9, 15, 6, 29, tzinfo=timezone.utc)

        host = await _claim_host(database, 7, start, start + timedelta(hours=6))

        self.assertEqual(host["connection_id"], 1)
        self.assertIsNone(database.parameters["exclude_id"])
        self.assertIn("CAST(:exclude_id AS BIGINT)", database.sql)


if __name__ == "__main__":
    unittest.main()
