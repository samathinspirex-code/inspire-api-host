import unittest
from datetime import datetime, timedelta, timezone

from app.modules.lms.zoom_service import _claim_host


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
    async def test_new_meeting_gives_nullable_exclusion_parameter_a_bigint_type(self):
        database = _Database()
        start = datetime(2026, 9, 15, 6, 29, tzinfo=timezone.utc)

        host = await _claim_host(database, 7, start, start + timedelta(hours=6))

        self.assertEqual(host["connection_id"], 1)
        self.assertIsNone(database.parameters["exclude_id"])
        self.assertIn("CAST(:exclude_id AS BIGINT)", database.sql)


if __name__ == "__main__":
    unittest.main()
