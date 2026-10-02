import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.modules.lms import zoom_service


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class _Database:
    def __init__(self, context, roster):
        self.context = context
        self.roster = roster
        self.statements = []

    async def execute(self, statement, parameters):
        sql = str(statement)
        self.statements.append((sql, parameters))
        if "FROM lms_online_meetings m" in sql:
            return _Rows([self.context])
        if "FROM lms_zoom_host_connections" in sql:
            return _Rows([{"connection_id": 1}])
        if "FROM lms_class_students cs" in sql:
            return _Rows(self.roster)
        return _Rows([])

    async def scalar(self, statement, parameters):
        self.statements.append((str(statement), parameters))
        return 42

    async def commit(self):
        pass


class ZoomAttendanceRosterTests(unittest.IsolatedAsyncioTestCase):
    def test_unique_short_zoom_name_matches_enrolled_student(self):
        roster = [{"user_id": 1, "full_name": "Chinthaka Nuwan"},
                  {"user_id": 2, "full_name": "Another Student"}]
        self.assertEqual(zoom_service._student_for_zoom_name("Nuwan", roster)["user_id"], 1)

    async def test_matches_audience_students_and_restored_enrolments(self):
        start = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
        end = start + timedelta(hours=1)
        db = _Database(
            {"meeting_id": 3, "class_id": 10, "zoom_host_connection_id": 1,
             "start_time": start, "end_time": end, "provider_meeting_uuid": "uuid",
             "attendance_threshold_percentage": 50},
            [{"user_id": 7, "full_name": "Aamina Shakir", "is_active": False,
              "assigned_at": end + timedelta(days=1)},
             {"user_id": 8, "full_name": "Later Student", "is_active": True,
              "assigned_at": end + timedelta(days=1)}],
        )
        participants = [{"name": "Aamina Shakir", "join_time": start.isoformat(),
                         "leave_time": end.isoformat(), "duration": 3600}]
        with patch.object(zoom_service, "_access_token", new=AsyncMock(return_value="token")), \
             patch.object(zoom_service, "_past_participants", new=AsyncMock(return_value=participants)):
            await zoom_service.sync_attendance(db, 3, 99)

        roster_sql = next(sql for sql, _ in db.statements if "FROM lms_class_students cs" in sql)
        self.assertIn("lms_meeting_audience_classes", roster_sql)
        self.assertIn("lms_attendance_records", roster_sql)
        self.assertNotIn("cs.assigned_at<=", roster_sql)
        session_params = next(params for sql, params in db.statements if "INSERT INTO lms_attendance_sessions" in sql)
        self.assertEqual(session_params["unmatched"], "[]")
        records = [params for sql, params in db.statements if "INSERT INTO lms_attendance_records" in sql]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["student"], 7)
        self.assertEqual(records[0]["seconds"], 3600)
        self.assertEqual(records[0]["status"], "present")

    async def test_queues_one_time_repair_for_previous_zero_minute_matches(self):
        class RepairDatabase:
            def __init__(self):
                self.statements = []
                self.commits = 0

            async def scalar(self, statement):
                return True

            async def execute(self, statement, parameters):
                sql = str(statement)
                self.statements.append((sql, parameters))
                return _Rows([{"meeting_id": 3}]) if "SELECT DISTINCT m.meeting_id" in sql else _Rows([])

            async def commit(self):
                self.commits += 1

        db = RepairDatabase()
        queued = await zoom_service.sweep_mismatched_attendance(
            db, datetime(2026, 10, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(queued, 1)
        self.assertIn("ar.attended_seconds=0", db.statements[0][0])
        self.assertIn("jsonb_array_elements", db.statements[0][0])
        self.assertEqual(db.statements[1][1]["key"], "repair:attendance-roster-v2:3")
        self.assertEqual(db.commits, 1)


if __name__ == "__main__":
    unittest.main()
