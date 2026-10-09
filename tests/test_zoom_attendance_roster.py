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
    def __init__(self, context, roster, lecturers=None, host=None):
        self.context = context
        self.roster = roster
        self.lecturers = lecturers or []
        self.host = host or {"connection_id": 1}
        self.statements = []

    async def execute(self, statement, parameters):
        sql = str(statement)
        self.statements.append((sql, parameters))
        if "FROM lms_online_meetings m" in sql:
            return _Rows([self.context])
        if "FROM lms_zoom_host_connections" in sql:
            return _Rows([self.host])
        if "FROM lms_class_students cs" in sql:
            return _Rows(self.roster)
        if "SELECT DISTINCT u.user_id,u.full_name,u.email FROM users u" in sql:
            return _Rows(self.lecturers)
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

    def test_staff_email_identifies_renamed_lecturer(self):
        staff, student = zoom_service._zoom_staff_identity(
            {"name": "Phone user", "user_email": "NISHADI@example.com"},
            [{"user_id": 99, "full_name": "Nishadi Charitha", "email": "nishadi@example.com"}],
            [{"user_id": 7, "full_name": "Phone User"}],
            {"email": "host@example.com"},
        )
        self.assertTrue(staff)
        self.assertIsNone(student)

    def test_ambiguous_lecturer_student_name_is_not_assigned(self):
        staff, student = zoom_service._zoom_staff_identity(
            {"name": "Nishadi Charitha"},
            [{"user_id": 99, "full_name": "Nishadi Charitha", "email": "nishadi@example.com"}],
            [{"user_id": 7, "full_name": "Nishadi Charitha"}],
            {},
        )
        self.assertTrue(staff)
        self.assertEqual(student["user_id"], 7)

    def test_exact_lecturer_name_beats_partial_student_name(self):
        staff, student = zoom_service._zoom_staff_identity(
            {"name": "Nishadi Charitha"},
            [{"user_id": 99, "full_name": "Nishadi Charitha", "email": "nishadi@example.com"}],
            [{"user_id": 7, "full_name": "Nishadi"}],
            {},
        )
        self.assertTrue(staff)
        self.assertIsNone(student)

    async def test_matches_audience_students_and_restored_enrolments(self):
        start = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
        end = start + timedelta(hours=1)
        db = _Database(
            {"meeting_id": 3, "class_id": 10, "zoom_host_connection_id": 1,
             "lecturer_user_id": 99,
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

    async def test_lecturer_rejoins_are_excluded_and_student_rejoins_add_up(self):
        start = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
        end = start + timedelta(hours=1)
        db = _Database(
            {"meeting_id": 3, "class_id": 10, "zoom_host_connection_id": 1,
             "lecturer_user_id": 99, "start_time": start, "end_time": end,
             "provider_meeting_uuid": "uuid", "attendance_threshold_percentage": 50},
            [{"user_id": 7, "full_name": "Aamina Shakir", "is_active": True,
              "assigned_at": start - timedelta(days=1)}],
            [{"user_id": 99, "full_name": "Nishadi Charitha", "email": "nishadi@example.com"}],
            {"connection_id": 1, "zoom_user_id": "zoom-host-id", "email": "host@example.com"},
        )
        def joined(name, first_minute, last_minute, **extra):
            return {"name": name, "join_time": (start + timedelta(minutes=first_minute)).isoformat(),
                    "leave_time": (start + timedelta(minutes=last_minute)).isoformat(),
                    "duration": (last_minute - first_minute) * 60, **extra}
        participants = [
            joined("Nishadi Charitha", 0, 20),
            joined("Nishadi Charitha", 25, 60),
            joined("Zoom Host", 0, 60, id="zoom-host-id"),
            joined("Aamina Shakir", 0, 20),
            joined("Aamina Shakir", 30, 50),
            joined("Unknown Guest", 10, 15),
        ]
        with patch.object(zoom_service, "_access_token", new=AsyncMock(return_value="token")), \
             patch.object(zoom_service, "_past_participants", new=AsyncMock(return_value=participants)):
            await zoom_service.sync_attendance(db, 3, 99)

        session_params = next(params for sql, params in db.statements if "INSERT INTO lms_attendance_sessions" in sql)
        self.assertIn("Unknown Guest", session_params["unmatched"])
        self.assertNotIn("Nishadi Charitha", session_params["unmatched"])
        self.assertNotIn("Zoom Host", session_params["unmatched"])
        records = [params for sql, params in db.statements if "INSERT INTO lms_attendance_records" in sql]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["student"], 7)
        self.assertEqual(records[0]["seconds"], 40 * 60)
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

    async def test_queues_reimport_of_existing_unmatched_sessions(self):
        class RepairDatabase:
            def __init__(self):
                self.statements = []

            async def scalar(self, statement):
                return True

            async def execute(self, statement, parameters):
                sql = str(statement)
                self.statements.append((sql, parameters))
                return _Rows([{"meeting_id": 3}]) if "SELECT m.meeting_id" in sql else _Rows([])

            async def commit(self):
                pass

        db = RepairDatabase()
        queued = await zoom_service.sweep_staff_attendance(db, datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertEqual(queued, 1)
        self.assertIn("jsonb_array_length", db.statements[0][0])
        self.assertEqual(db.statements[1][1]["key"], "repair:attendance-staff-v1:3")


if __name__ == "__main__":
    unittest.main()
