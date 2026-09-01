"""Real SQL regression tests; never connect to the configured application database."""
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import APIError, api_error_handler
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.auth.schemas import CurrentUser
from app.modules.cms.models import Program
from app.modules.lms import analytics_service, dashboard_service, service
from app.modules.lms.models import (
    AttendanceRecord, AttendanceSession, ClassStudent, CourseEnrollment, CourseLecturer,
    LecturerProfile, LmsClass, LmsCourse, LmsCourseworkAssignment, LmsCourseworkSubmission,
    LmsLearningItem, LmsLearningProgress, LmsModule, OnlineMeeting, StudentProfile,
)
from app.modules.lms.router import router


class LocalSession:
    def __init__(self, session):
        self.session = session

    async def execute(self, stmt):
        return self.session.execute(stmt)

    async def scalar(self, stmt):
        return self.session.scalar(stmt)

    async def scalars(self, stmt):
        return self.session.scalars(stmt)


class AdminDashboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        for model in [User, Program, StudentProfile, LecturerProfile, LmsCourse, LmsClass,
                      LmsModule, LmsLearningItem, OnlineMeeting, AttendanceSession, AttendanceRecord,
                      CourseEnrollment, CourseLecturer, ClassStudent, LmsLearningProgress,
                      LmsCourseworkAssignment, LmsCourseworkSubmission]:
            model.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.db = LocalSession(self.session)
        self.now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
        for module in [dashboard_service, analytics_service]:
            clock = patch.object(module, "datetime")
            clock.start().now.return_value = self.now
            self.addCleanup(clock.stop)
        self.queries = []
        event.listen(self.engine, "before_cursor_execute",
                     lambda conn, cursor, sql, params, ctx, many: self.queries.append(sql))

    def seed(self):
        db = self.session
        db.add(Program(program_id=1, slug="p1", title="Programme", code="P1", level="L5",
                       school="Computing", awarding_body="Test", duration="1 year",
                       price_from=0, icon="x", image_label="Test", blurb="Test"))
        for uid in [1, 2, 3, 10, 11, 99]:
            db.add(User(user_id=uid, email=f"u{uid}@example.test", is_active=uid != 2))
        for uid in [1, 2, 3]:
            db.add(StudentProfile(user_id=uid, student_number=f"S{uid}"))
        for uid in [10, 11]:
            db.add(LecturerProfile(user_id=uid, staff_number=f"L{uid}"))
        for cid, status in [(1, "active"), (2, "draft"), (3, "archived")]:
            db.add(LmsCourse(course_id=cid, program_id=1, code=f"C{cid}", title=f"Course {cid}", status=status, created_at=self.now))
        db.add_all([CourseLecturer(course_id=1, lecturer_user_id=10), CourseLecturer(course_id=2, lecturer_user_id=11)])
        db.add_all([CourseEnrollment(course_id=1, student_user_id=1, status="enrolled"),
                    CourseEnrollment(course_id=1, student_user_id=2, status="enrolled"),
                    CourseEnrollment(course_id=1, student_user_id=3, status="withdrawn"),
                    CourseEnrollment(course_id=2, student_user_id=1, status="enrolled")])
        for cid, status in [(1, "active"), (2, "planned"), (3, "completed")]:
            db.add(LmsClass(class_id=cid, course_id=1, code=f"CL{cid}", name=f"Class {cid}",
                            status=status, start_date=date(2026, 8, 1), end_date=date(2026, 12, 1)))
        for mid, cid, status in [(1, 1, "active"), (2, 1, "draft"), (3, 2, "active"), (4, 3, "active")]:
            db.add(LmsModule(module_id=mid, course_id=cid, title=f"Section {mid}", position=mid, status=status))
        for iid, mid, kind, status in [(1, 1, "video", "published"), (2, 1, "pdf", "published"),
                                       (3, 1, "text", "draft"), (4, 2, "video", "published"),
                                       (5, 3, "text", "published"), (6, 4, "pdf", "published")]:
            db.add(LmsLearningItem(learning_item_id=iid, module_id=mid, position=iid,
                                   item_type=kind, title=f"Item {iid}", status=status))
        for iid in [1, 2, 3, 4]:
            db.add(LmsLearningProgress(learning_item_id=iid, student_user_id=1, completion_percent=100,
                                       is_completed=True, completed_at=self.now, last_activity_at=self.now))
        db.add(LmsLearningProgress(learning_item_id=1, student_user_id=3, completion_percent=100,
                                   is_completed=True, completed_at=self.now, last_activity_at=self.now))
        for mid in range(1, 12):
            start = self.now + timedelta(hours=mid)
            end = start + timedelta(hours=1)
            if mid == 1: start, end = self.now - timedelta(minutes=10), self.now + timedelta(minutes=10)
            if mid == 3: start, end = self.now - timedelta(hours=2), self.now - timedelta(hours=1)
            db.add(OnlineMeeting(meeting_id=mid, class_id=1, lecturer_user_id=10, title=f"Meeting {mid}",
                                 start_time=start, end_time=end, timezone="Asia/Colombo",
                                 status="cancelled" if mid == 4 else "completed" if mid == 5 else "scheduled",
                                 google_space_name=f"spaces/{mid}", google_meeting_uri=f"https://meet.google.com/test-{mid}",
                                 google_meeting_code=f"test-{mid}", calendar_sync_status="disabled"))
        db.add(AttendanceSession(attendance_session_id=1, meeting_id=3, class_id=1, sync_status="synced"))
        db.add_all([AttendanceRecord(attendance_session_id=1, student_user_id=1, status="present"),
                    AttendanceRecord(attendance_session_id=1, student_user_id=2, status="absent")])
        for aid, released in [(1, True), (2, False)]:
            db.add(LmsCourseworkAssignment(assignment_id=aid, course_id=1, target_id=1, title="Assignment",
                                           instructions="Instructions", created_by=10, grades_released=released))
            db.add(LmsCourseworkSubmission(assignment_id=aid, student_user_id=1, started_at=self.now,
                                           submitted_at=self.now, marks_awarded=80 if released else 20, status="reviewed"))
        db.commit()
        self.queries.clear()

    async def test_empty_dashboard_has_zero_counts_but_no_fake_attendance_rate(self):
        result = await dashboard_service.get_admin_dashboard(self.db)
        self.assertEqual(result.total_students, 0)
        self.assertEqual(result.active_courses, 0)
        self.assertEqual(result.upcoming_meetings, [])
        self.assertEqual(result.recent_courses, [])
        self.assertIsNone(result.attendance_rate)
        self.assertEqual(len(self.queries), 4)

    async def test_live_counts_and_bounded_upcoming_schedule(self):
        self.seed()
        result = await dashboard_service.get_admin_dashboard(self.db)
        self.assertEqual((result.total_students, result.total_lecturers, result.total_programmes), (3, 2, 1))
        self.assertEqual((result.active_courses, result.active_classes, result.published_content), (1, 1, 5))
        self.assertEqual((result.attendance_rate, result.attendance_records), (50, 2))
        self.assertEqual(result.upcoming_classes, 8)
        self.assertEqual([m.meeting_id for m in result.upcoming_meetings], [1, 2, 6, 7, 8])
        self.assertTrue(all(m.start_time.tzinfo is not None for m in result.upcoming_meetings))
        self.assertEqual([c.course_id for c in result.recent_courses], [3, 2, 1])
        self.assertEqual(len(self.queries), 4)
        self.assertNotIn("email", result.model_dump_json())

    async def test_status_changes_additions_and_deletions_refresh_totals(self):
        self.seed()
        await dashboard_service.get_admin_dashboard(self.db)
        self.session.execute(update(LmsCourse).where(LmsCourse.course_id == 2).values(status="active"))
        self.session.execute(update(LmsClass).where(LmsClass.class_id == 2).values(status="active"))
        self.session.execute(delete(LmsLearningItem).where(LmsLearningItem.learning_item_id == 2))
        self.session.execute(update(OnlineMeeting).where(OnlineMeeting.meeting_id == 2).values(status="cancelled"))
        self.session.add(User(user_id=50, email="new@example.test"))
        self.session.add(StudentProfile(user_id=50, student_number="S50"))
        self.session.commit()
        result = await dashboard_service.get_admin_dashboard(self.db)
        self.assertEqual((result.total_students, result.active_courses, result.active_classes, result.published_content, result.upcoming_classes), (4, 2, 2, 4, 7))

    async def test_reports_use_active_sections_and_current_enrolments(self):
        self.seed()
        result = await analytics_service.get_dashboard(self.db, 99, "ADMIN")
        first = next(c for c in result.course_insights if c.course_id == 1)
        self.assertEqual(first.progress, 50)  # two items x two students; withdrawn progress excluded
        self.assertEqual(first.grade_average, 80)  # unreleased 20 must not leak
        self.assertEqual(first.attendance, 50)
        self.assertNotIn(3, [c.course_id for c in result.course_insights])
        self.assertEqual(next(m for m in result.metrics if m.key == "students").value, 2)
        self.assertEqual(sum(p.completions for p in result.weekly_trend), 2)

    async def test_reports_retain_lecturer_and_student_scope(self):
        self.seed()
        lecturer = await analytics_service.get_dashboard(self.db, 10, "LECTURER")
        self.assertEqual([c.course_id for c in lecturer.course_insights], [1])
        student = await analytics_service.get_dashboard(self.db, 1, "STUDENT")
        first = next(c for c in student.course_insights if c.course_id == 1)
        self.assertEqual(first.progress, 100)
        self.assertEqual(first.attendance, 100)
        self.assertEqual(first.grade_average, 80)


class AdminDashboardAccessTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.add_exception_handler(APIError, api_error_handler)
        self.app.include_router(router)
        self.app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def user(self, access):
        self.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=1, email="test@example.test", access=access)

    def test_anonymous_and_non_admin_access_denied(self):
        self.assertEqual(self.client.get("/api/v1/lms/admin/dashboard").status_code, 401)
        for access in [["LMS", "STUDENT"], ["LMS", "LECTURER"], ["CMS", "ADMIN"], ["LMS"]]:
            with self.subTest(access=access):
                self.user(access)
                with patch.object(dashboard_service, "get_admin_dashboard", new_callable=AsyncMock) as load:
                    self.assertEqual(self.client.get("/api/v1/lms/admin/dashboard").status_code, 403)
                    load.assert_not_awaited()

    def test_both_admin_roles_receive_live_endpoint_not_bootstrap_placeholders(self):
        payload = dict(total_students=5, total_lecturers=2, total_programmes=1, active_courses=3,
                       active_classes=1, published_content=8, upcoming_classes=0, attendance_rate=None,
                       attendance_records=0, upcoming_meetings=[], recent_courses=[], generated_at="2026-08-31T12:00:00Z")
        for role in ["ADMIN", "SUPER_ADMIN"]:
            with self.subTest(role=role):
                self.user(["LMS", role])
                with patch.object(dashboard_service, "get_admin_dashboard", new_callable=AsyncMock, return_value=payload):
                    response = self.client.get("/api/v1/lms/admin/dashboard")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["total_students"], 5)
                self.assertEqual(response.headers["cache-control"], "no-store")
                bootstrap = service.build_bootstrap(CurrentUser(user_id=1, email="test@example.test", access=["LMS", role]))
                self.assertEqual(bootstrap.metrics, [])
                self.assertTrue(bootstrap.navigation)
