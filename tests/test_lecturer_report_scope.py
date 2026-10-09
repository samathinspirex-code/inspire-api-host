"""Report filters follow current class assignments rather than meeting hosts."""

import unittest
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.cms.models import Program
from app.modules.lms.models import (
    AttendanceRecord, AttendanceSession, ClassLecturer, ClassStudent, CourseEnrollment,
    LecturerProfile, LmsClass, LmsCourse, OnlineMeeting, StudentProfile,
)
from app.modules.lms import attendance_service, portal_service
from app.modules.lms.repository.attendance import AttendanceRepository
from app.modules.lms.repository.portal import PortalRepository


class LocalSession:
    def __init__(self, engine):
        self.session = Session(engine)

    async def execute(self, statement):
        return self.session.execute(statement)


class LecturerReportScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        for model in (
            User, Program, LmsCourse, LmsClass, ClassLecturer, ClassStudent,
            CourseEnrollment,
            LecturerProfile, StudentProfile, OnlineMeeting, AttendanceSession,
            AttendanceRecord,
        ):
            model.__table__.create(self.engine)

        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        with Session(self.engine) as db:
            db.add(Program(
                program_id=1, slug="current", title="Current programme", code="CURRENT",
                level="L5", school="Test", awarding_body="Test", duration="One year",
                price_from=0, icon="x", image_label="Test", blurb="Test",
            ))
            for user_id in (10, 11, 20):
                db.add(User(user_id=user_id, email=f"user{user_id}@example.test", full_name=f"User {user_id}"))
            for user_id in (10, 11):
                db.add(LecturerProfile(user_id=user_id, staff_number=f"L{user_id}"))
            db.add(StudentProfile(user_id=20, student_number="S20"))
            for course_id, status in ((1, "active"), (2, "active"), (3, "archived"), (4, "active")):
                db.add(LmsCourse(
                    course_id=course_id, program_id=1, code=f"C{course_id}",
                    title=f"Course {course_id}", status=status,
                ))
                db.add(LmsClass(
                    class_id=course_id, course_id=course_id, code=f"B{course_id}",
                    name=f"Batch {course_id}", start_date=now.date(),
                    end_date=(now + timedelta(days=30)).date(),
                    status="cancelled" if course_id == 4 else "active",
                ))
                db.add(ClassLecturer(
                    class_id=course_id, lecturer_user_id=11 if course_id == 2 else 10,
                ))
                db.add(OnlineMeeting(
                    meeting_id=course_id, class_id=course_id,
                    lecturer_user_id=10 if course_id == 2 else 11,
                    title=f"Meeting {course_id}", start_time=now,
                    end_time=now + timedelta(hours=1), timezone="Asia/Colombo",
                    status="completed", calendar_sync_status="disabled",
                ))
                db.add(AttendanceSession(
                    attendance_session_id=course_id, meeting_id=course_id,
                    class_id=course_id, sync_status="synced",
                ))
                db.add(AttendanceRecord(
                    attendance_record_id=course_id, attendance_session_id=course_id,
                    student_user_id=20, status="present",
                ))
                db.add(CourseEnrollment(
                    course_id=course_id, student_user_id=20, status="enrolled",
                ))
                if course_id != 2:
                    db.add(ClassStudent(class_id=course_id, student_user_id=20))
            db.commit()

    def db(self):
        db = LocalSession(self.engine)
        self.addCleanup(db.session.close)
        return db

    async def test_lecturer_options_use_assigned_active_classes(self):
        repository = AttendanceRepository(self.db())
        options = await repository.report_options(lecturer_scope_user_id=10)
        self.assertEqual([row[0] for row in options["courses"]], [1])
        self.assertEqual([row[0] for row in options["classes"]], [1])
        self.assertEqual([row[0] for row in options["lecturers"]], [11])
        records = await repository.list_report(lecturer_scope_user_id=10)
        self.assertEqual([row[0].attendance_record_id for row in records], [1])
        lecturer_options = await attendance_service.get_attendance_report_options(self.db(), 10, "LECTURER")
        self.assertEqual([item.value for item in lecturer_options.classes], [1])
        self.assertEqual(lecturer_options.lecturers, [])

        manager_options = await AttendanceRepository(self.db()).report_options()
        self.assertEqual([row[0] for row in manager_options["courses"]], [1, 2])

    async def test_academic_class_options_exclude_archived_and_cancelled(self):
        rows = await PortalRepository(self.db()).list_classes(10, "LECTURER")
        self.assertEqual([row[0].class_id for row in rows], [1])
        self.assertEqual(rows[0][5], 1)
        response = await portal_service.list_my_classes(self.db(), 10, "LECTURER")
        self.assertEqual(response.data[0].program_id, 1)

    async def test_student_history_uses_current_class_and_course_enrolments(self):
        response = await attendance_service.list_my_attendance(self.db(), 20)
        self.assertEqual([item.attendance_record_id for item in response.data], [1])
        self.assertEqual(response.total_sessions, 1)

        with Session(self.engine) as db:
            db.get(CourseEnrollment, (1, 20)).status = "withdrawn"
            db.commit()
        withdrawn = await attendance_service.list_my_attendance(self.db(), 20)
        self.assertEqual(withdrawn.data, [])
        self.assertEqual(withdrawn.total_sessions, 0)


if __name__ == "__main__":
    unittest.main()
