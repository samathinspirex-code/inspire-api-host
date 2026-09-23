"""Student calendar must contain only current, relevant class meetings."""
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.cms.models import Program
from app.modules.lms.models import (
    ClassStudent,
    CourseEnrollment,
    LmsClass,
    LmsCourse,
    OnlineMeeting,
)
from app.modules.lms.repository.meeting import MeetingRepository


class LocalSession:
    def __init__(self, engine):
        self.session = Session(engine)

    async def execute(self, statement):
        return self.session.execute(statement)


class StudentCalendarScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        for model in [Program, LmsCourse, LmsClass, CourseEnrollment, ClassStudent, OnlineMeeting]:
            model.__table__.create(self.engine)

        now = datetime.now(timezone.utc)
        with Session(self.engine) as db:
            db.add(Program(program_id=1, slug="calendar", title="Calendar", code="CAL", level="L5",
                           school="Test", awarding_body="Test", duration="One year", price_from=0,
                           icon="x", image_label="Calendar", blurb="Calendar"))
            db.add_all([
                LmsCourse(course_id=1, program_id=1, code="OWN", title="Own course", status="active"),
                LmsCourse(course_id=2, program_id=1, code="OTHER", title="Other course", status="active"),
                LmsCourse(course_id=3, program_id=1, code="OLD", title="Archived course", status="archived"),
                LmsCourse(course_id=4, program_id=1, code="LEFT", title="Withdrawn course", status="active"),
            ])
            db.add_all([
                self._class(1, 1, "active", now),
                self._class(2, 2, "active", now),
                self._class(3, 1, "cancelled", now),
                self._class(4, 3, "active", now),
                self._class(5, 1, "planned", now),
                self._class(6, 4, "active", now),
            ])
            db.add_all([
                CourseEnrollment(course_id=1, student_user_id=20, status="enrolled"),
                CourseEnrollment(course_id=2, student_user_id=20, status="enrolled"),
                CourseEnrollment(course_id=3, student_user_id=20, status="enrolled"),
                CourseEnrollment(course_id=4, student_user_id=20, status="withdrawn"),
            ])
            db.add_all([ClassStudent(class_id=class_id, student_user_id=20) for class_id in (1, 3, 4, 5, 6)])
            db.add_all([
                self._meeting(1, 1, "scheduled", now + timedelta(days=1)),
                self._meeting(2, 1, "cancelled", now + timedelta(days=2)),
                self._meeting(3, 1, "completed", now - timedelta(days=1)),
                self._meeting(4, 2, "scheduled", now + timedelta(days=1)),
                self._meeting(5, 3, "scheduled", now + timedelta(days=1)),
                self._meeting(6, 4, "scheduled", now + timedelta(days=1)),
                self._meeting(7, 5, "scheduled", now + timedelta(days=3)),
                self._meeting(8, 6, "scheduled", now + timedelta(days=1)),
                self._meeting(9, 1, "scheduled", now - timedelta(days=3)),
            ])
            db.commit()

    @staticmethod
    def _class(class_id, course_id, status, now):
        return LmsClass(class_id=class_id, course_id=course_id, code=f"CLASS-{class_id}",
                        name=f"Class {class_id}", start_date=now.date(),
                        end_date=(now + timedelta(days=90)).date(), status=status)

    @staticmethod
    def _meeting(meeting_id, class_id, status, start):
        return OnlineMeeting(meeting_id=meeting_id, class_id=class_id, lecturer_user_id=10,
                             title=f"Meeting {meeting_id}", start_time=start,
                             end_time=start + timedelta(hours=1), timezone="Asia/Colombo",
                             status=status, provider="google", join_uri="https://meet.example/test",
                             calendar_sync_status="disabled")

    async def test_student_sees_only_upcoming_meetings_for_current_classes(self):
        session = LocalSession(self.engine)
        self.addCleanup(session.session.close)

        rows = await MeetingRepository(session).list_for_user(20, "STUDENT")

        self.assertEqual([row[0].meeting_id for row in rows], [7, 1])


if __name__ == "__main__":
    unittest.main()
