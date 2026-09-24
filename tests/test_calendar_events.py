import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError
from app.modules.auth.models import User
from app.modules.cms.models import Program
from app.modules.lms.calendar_event_service import create_event, list_events
from app.modules.lms.models import (
    ClassLecturer,
    ClassStudent,
    CourseEnrollment,
    CourseLecturer,
    LecturerProfile,
    LmsCalendarEvent,
    LmsClass,
    LmsCourse,
    StudentProfile,
)
from app.modules.lms.schemas.calendar_event import CalendarEventWrite


class LocalSession:
    def __init__(self, engine):
        self.session = Session(engine)

    async def execute(self, statement):
        return self.session.execute(statement)

    async def get(self, model, key):
        return self.session.get(model, key)

    async def scalar(self, statement):
        return self.session.scalar(statement)

    def add(self, item):
        self.session.add(item)

    async def commit(self):
        self.session.commit()

    async def refresh(self, item):
        self.session.refresh(item)


class CalendarEventTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        for model in [User, Program, StudentProfile, LecturerProfile, LmsCourse, LmsClass, CourseEnrollment, ClassStudent, CourseLecturer, ClassLecturer, LmsCalendarEvent]:
            model.__table__.create(self.engine)
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db:
            db.add_all([
                Program(program_id=1, slug="business", title="Business", code="BUS", level="L5", school="Main", awarding_body="Inspire", duration="One year", price_from=0, icon="x", image_label="Business", blurb="Business"),
                Program(program_id=2, slug="it", title="Computing", code="IT", level="L5", school="Main", awarding_body="Inspire", duration="One year", price_from=0, icon="x", image_label="Computing", blurb="Computing"),
            ])
            db.add_all([
                LmsCourse(course_id=1, program_id=1, code="BUS1", title="Business", status="active"),
                LmsCourse(course_id=2, program_id=2, code="IT1", title="Computing", status="active"),
            ])
            db.add_all([
                LmsClass(class_id=1, course_id=1, code="BUS-SEP", name="Sep_2026", start_date=now.date(), end_date=(now + timedelta(days=90)).date()),
                LmsClass(class_id=2, course_id=2, code="IT-SEP", name="Sep_2026", start_date=now.date(), end_date=(now + timedelta(days=90)).date()),
            ])
            db.add(CourseEnrollment(course_id=1, student_user_id=20, status="enrolled"))
            db.add(ClassStudent(class_id=1, student_user_id=20))
            db.add(ClassLecturer(class_id=1, lecturer_user_id=10))
            start = now + timedelta(days=1)
            db.add_all([
                self._event(1, "University day", "university", None, None, start),
                self._event(2, "Business briefing", "programme", 1, None, start),
                self._event(3, "Computing briefing", "programme", 2, None, start),
                self._event(4, "Class workshop", "class", None, 1, start),
                self._event(5, "Other class", "class", None, 2, start),
            ])
            db.commit()

    @staticmethod
    def _event(event_id, title, audience, program_id, class_id, start):
        return LmsCalendarEvent(
            event_id=event_id, title=title, audience_type=audience, program_id=program_id, class_id=class_id,
            start_time=start, end_time=start + timedelta(hours=1), status="scheduled", created_by=10,
        )

    def db(self):
        db = LocalSession(self.engine)
        self.addCleanup(db.session.close)
        return db

    async def test_student_sees_university_own_programme_and_own_class_only(self):
        result = await list_events(self.db(), 20, "STUDENT")
        self.assertEqual({item.title for item in result.data}, {"University day", "Business briefing", "Class workshop"})

    async def test_lecturer_cannot_publish_a_university_event(self):
        start = datetime.now(timezone.utc) + timedelta(days=2)
        payload = CalendarEventWrite(
            title="Closed campus", start_time=start, end_time=start + timedelta(hours=1), audience_type="university",
        )
        with self.assertRaises(ForbiddenError):
            await create_event(self.db(), payload, 10, "LECTURER")

    async def test_admin_university_event_is_visible_to_a_student(self):
        start = datetime.now(timezone.utc) + timedelta(days=3)
        payload = CalendarEventWrite(
            title="Graduation", start_time=start, end_time=start + timedelta(hours=2), audience_type="university",
        )
        created = await create_event(self.db(), payload, 1, "ADMIN")
        self.assertEqual(created.audience_type, "university")
        titles = {item.title for item in (await list_events(self.db(), 20, "STUDENT")).data}
        self.assertIn("Graduation", titles)
