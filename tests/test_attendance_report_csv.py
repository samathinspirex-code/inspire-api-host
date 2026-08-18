import csv
import unittest
from datetime import datetime, timezone
from io import StringIO

from app.modules.lms.attendance_service import build_attendance_report_csv
from app.modules.lms.schemas import AttendanceReportItem


class AttendanceReportCsvTests(unittest.TestCase):
    def test_export_uses_separate_columns_and_escapes_formula_values(self):
        now = datetime(2026, 8, 11, 8, 30, tzinfo=timezone.utc)
        item = AttendanceReportItem(
            attendance_record_id=1,
            meeting_id=2,
            meeting_title="=Unsafe meeting title",
            meeting_start_time=now,
            meeting_end_time=now,
            program_id=3,
            program_code="HND",
            program_title="Computing",
            course_id=4,
            course_code="SE",
            course_title="Software Engineering",
            class_id=5,
            class_code="SE-01",
            class_name="Cohort 1",
            lecturer_user_id=6,
            lecturer_staff_number="LEC-01",
            lecturer_name="Lecturer",
            lecturer_email="lecturer@example.com",
            student_user_id=7,
            student_number="STU-01",
            student_name="Student",
            student_email="student@example.com",
            status="present",
            attended_seconds=3600,
            attendance_percentage=100,
            first_join_time=now,
            last_leave_time=now,
            source="google_meet",
            override_reason=None,
            synced_at=now,
        )

        rows = list(csv.reader(StringIO(build_attendance_report_csv([item]))))

        self.assertEqual(len(rows[0]), 30)
        self.assertEqual(len(rows[1]), 30)
        self.assertEqual(rows[0][9], "meeting_title")
        self.assertEqual(rows[1][9], "'=Unsafe meeting title")
        self.assertEqual(rows[1][27], "Student")
        self.assertEqual(rows[1][28], "student@example.com")


if __name__ == "__main__":
    unittest.main()
