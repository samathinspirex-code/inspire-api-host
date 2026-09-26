"""Lecturer Zoom access follows current class assignments and hides cancellations."""
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.modules.auth.models.user import User
from app.modules.cms.models import Program
from app.modules.lms.models import (
    ClassLecturer,
    ClassStudent,
    CourseLecturer,
    LmsClass,
    LmsCourse,
    OnlineMeeting,
)
from app.modules.lms.repository.meeting import MeetingRepository
from app.modules.lms.zoom_service import _lecturer_can_host


class LocalSession:
    def __init__(self, engine):
        self.session = Session(engine)

    async def execute(self, statement, params=None):
        return self.session.execute(statement, params or {})

    async def scalar(self, statement, params=None):
        return self.session.scalar(statement, params or {})


class LecturerZoomAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        for model in [User, Program, LmsCourse, LmsClass, ClassStudent, ClassLecturer, CourseLecturer, OnlineMeeting]:
            model.__table__.create(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("""CREATE TABLE lms_meeting_audience_classes (
                meeting_id BIGINT NOT NULL, class_id BIGINT NOT NULL,
                PRIMARY KEY (meeting_id, class_id)
            )"""))

        now = datetime.now(timezone.utc)
        with Session(self.engine) as db:
            db.add(Program(program_id=1, slug="zoom", title="Zoom", code="ZOOM", level="L4",
                           school="Test", awarding_body="Test", duration="One year", price_from=0,
                           icon="x", image_label="Zoom", blurb="Zoom"))
            db.add_all([
                LmsCourse(course_id=1, program_id=1, code="ONE", title="Course one", status="active"),
                LmsCourse(course_id=2, program_id=1, code="TWO", title="Course two", status="active"),
            ])
            db.add_all([
                self._class(1, 1, now), self._class(2, 2, now), self._class(3, 2, now),
            ])
            db.add_all([
                ClassLecturer(class_id=1, lecturer_user_id=10),
                CourseLecturer(course_id=2, lecturer_user_id=20),
            ])
            db.add_all([
                self._meeting(1, 1, 99, "scheduled", now + timedelta(days=1)),
                self._meeting(2, 1, 99, "cancelled", now + timedelta(days=2)),
                self._meeting(3, 2, 99, "scheduled", now + timedelta(days=3)),
                self._meeting(4, 3, 30, "scheduled", now + timedelta(days=4)),
            ])
            db.commit()
            for meeting_id, class_id in [(1, 1), (2, 1), (3, 2), (4, 3)]:
                db.execute(text("INSERT INTO lms_meeting_audience_classes VALUES (:m,:c)"), {"m": meeting_id, "c": class_id})
            db.commit()

    @staticmethod
    def _class(class_id, course_id, now):
        return LmsClass(class_id=class_id, course_id=course_id, code=f"C-{class_id}",
                        name=f"Class {class_id}", start_date=now.date(),
                        end_date=(now + timedelta(days=90)).date(), status="active")

    @staticmethod
    def _meeting(meeting_id, class_id, lecturer_id, status, start):
        return OnlineMeeting(meeting_id=meeting_id, class_id=class_id,
                             lecturer_user_id=lecturer_id, title=f"Meeting {meeting_id}",
                             start_time=start, end_time=start + timedelta(hours=1),
                             timezone="Asia/Colombo", status=status, provider="zoom",
                             join_uri="", calendar_sync_status="disabled")

    async def test_only_current_class_lecturers_are_zoom_hosts(self):
        session = LocalSession(self.engine)
        self.addCleanup(session.session.close)
        self.assertTrue(await _lecturer_can_host(session, 1, 1, 99, 10))
        self.assertFalse(await _lecturer_can_host(session, 3, 2, 99, 20))
        self.assertFalse(await _lecturer_can_host(session, 4, 3, 30, 30))
        self.assertFalse(await _lecturer_can_host(session, 1, 1, 99, 40))

    async def test_cancelled_meetings_are_not_listed_for_lecturers(self):
        session = LocalSession(self.engine)
        self.addCleanup(session.session.close)
        rows = await MeetingRepository(session).list_for_user(10, "LECTURER")
        self.assertEqual([row[0].meeting_id for row in rows], [1])

        removed_rows = await MeetingRepository(session).list_for_user(20, "LECTURER")
        self.assertEqual(removed_rows, [])


if __name__ == "__main__":
    unittest.main()
