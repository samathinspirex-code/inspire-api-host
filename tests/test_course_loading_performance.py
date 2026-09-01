"""Offline regression tests: real queries, no access to configured/cloud databases."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.auth.models import User
from app.modules.cms.models import Program
from app.modules.lms import content_service, progress_service
from app.modules.lms.models import (
    CourseEnrollment, CourseLecturer, ClassLecturer, ClassStudent, LmsClass, LmsCourse,
    LmsCourseDiscussion, LmsLearningItem, LmsLearningProgress, LmsLectureQuizAttempt, LmsModule,
)
from app.modules.lms.repository import ContentRepository
from app.modules.lms.repository.portal import PortalRepository


class LocalSession:
    def __init__(self, engine):
        self.session = Session(engine)

    async def execute(self, stmt):
        return self.session.execute(stmt)

    async def get(self, model, key):
        return self.session.get(model, key)

    async def scalar(self, stmt):
        return self.session.scalar(stmt)


class PerSectionRepository(ContentRepository):
    async def list_items_for_modules(self, ids):
        return {mid: await self.list_items(mid) for mid in ids}


class CourseLoadingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        for model in [User, Program, LmsCourse, CourseEnrollment, CourseLecturer, LmsModule,
                      LmsLearningItem, LmsLearningProgress, LmsLectureQuizAttempt, LmsCourseDiscussion,
                      ClassLecturer, ClassStudent, LmsClass]:
            model.__table__.create(self.engine)
        with Session(self.engine) as db:
            db.add(Program(program_id=1, slug="test", title="Test", code="TEST", level="L5", school="Test",
                           awarding_body="Test", duration="One year", price_from=0, icon="x", image_label="Test", blurb="Test"))
            for cid in [1, 2]:
                db.add(LmsCourse(course_id=cid, program_id=1, code=f"C{cid}", title=f"Course {cid}", status="active"))
            db.add(CourseLecturer(course_id=1, lecturer_user_id=10))
            db.add(CourseLecturer(course_id=2, lecturer_user_id=11))
            db.add_all([User(user_id=10, email="teacher@example.test", full_name="Teacher"),
                        User(user_id=11, email="other@example.test", full_name="Other teacher")])
            for sid in range(20, 70):
                db.add(User(user_id=sid, email=f"student{sid}@example.test"))
                db.add(CourseEnrollment(course_id=1, student_user_id=sid, status="enrolled"))
            db.add(CourseEnrollment(course_id=1, student_user_id=99, status="withdrawn"))
            db.add(CourseEnrollment(course_id=2, student_user_id=99, status="enrolled"))
            for mid in range(1, 13):
                db.add(LmsModule(module_id=mid, course_id=1, title=f"Section {mid}", position=mid, status="active"))
                db.add(LmsLearningItem(learning_item_id=mid, module_id=mid, title=f"Item {mid}", position=1, item_type="text", status="published"))
            db.add(LmsModule(module_id=13, course_id=1, title="Draft section", position=13, status="draft"))
            db.add(LmsModule(module_id=14, course_id=2, title="Other course", position=1, status="active"))
            for iid, mid, status in [(13, 13, "published"), (14, 14, "published"), (15, 1, "draft")]:
                db.add(LmsLearningItem(learning_item_id=iid, module_id=mid, title="Excluded item", position=2, item_type="text", status=status))
            for sid in [20, 99]:
                for iid in range(1, 16):
                    db.add(LmsLearningProgress(learning_item_id=iid, student_user_id=sid, completion_percent=50, is_completed=False))
            now = datetime.now(timezone.utc)
            for did in range(1, 206):
                db.add(LmsCourseDiscussion(discussion_id=did, course_id=1, author_user_id=10 if did % 2 else 20,
                                          message=f"Message {did}", created_at=now + timedelta(seconds=did)))
            # A lecturer of a different course must not be labelled as this course's lecturer.
            db.add(LmsCourseDiscussion(discussion_id=206, course_id=1, author_user_id=11, message="Other teacher", created_at=now + timedelta(seconds=206)))
            db.add(LmsCourseDiscussion(discussion_id=207, course_id=2, author_user_id=11, message="Private course", created_at=now))
            db.commit()
        self.queries = []
        event.listen(self.engine, "before_cursor_execute", lambda conn, cursor, sql, params, ctx, many: self.queries.append(sql))

    def db(self):
        db = LocalSession(self.engine)
        self.addCleanup(db.session.close)
        return db

    async def test_roster_50_students_in_four_reads_matches_detailed_percentage(self):
        result = await progress_service.get_course_progress_summary(self.db(), 1, 10)
        self.assertEqual(len(self.queries), 4)
        self.assertEqual(len(result.data), 50)
        by_id = {row.student_user_id: row.completion_percent for row in result.data}
        self.assertEqual(by_id[20], 50)
        self.assertEqual(by_id[21], 0)
        self.assertNotIn(99, by_id)
        detail = await progress_service.get_course_progress(self.db(), 1, 20, 10, "LECTURER")
        self.assertEqual(detail.total_items, 12)
        self.assertEqual(detail.completion_percent, by_id[20])

    async def test_detailed_progress_batches_sections_without_changing_result(self):
        with patch.object(progress_service, "ContentRepository", PerSectionRepository):
            before = await progress_service.get_course_progress(self.db(), 1, 20, 10, "LECTURER")
        baseline = len(self.queries)
        self.queries.clear()
        after = await progress_service.get_course_progress(self.db(), 1, 20, 10, "LECTURER")
        self.assertEqual(before, after)
        self.assertEqual(len(self.queries), 7)
        self.assertGreater(baseline, len(self.queries))

    async def test_empty_course_summary_reports_zero(self):
        with Session(self.engine) as db:
            db.get(LmsModule, 14).status = "draft"
            db.commit()
        result = await progress_service.get_course_progress_summary(self.db(), 2, 11)
        self.assertEqual(result.data[0].completion_percent, 0)

    async def test_roster_and_progress_permissions_are_preserved(self):
        for requester in [11, 20]:
            with self.assertRaises(ForbiddenError):
                await progress_service.get_course_progress_summary(self.db(), 1, requester)
        with self.assertRaises(NotFoundError):
            await progress_service.get_course_progress(self.db(), 1, 99, 10, "LECTURER")
        with self.assertRaises(ForbiddenError):
            await progress_service.get_course_progress(self.db(), 1, 21, 20, "STUDENT")

    async def test_discussions_latest_200_in_three_reads_and_course_scoped_roles(self):
        response = await content_service.list_course_discussions(self.db(), 1, 20, "STUDENT")
        self.assertEqual(len(self.queries), 3)
        self.assertEqual([row.discussion_id for row in response.data], list(range(7, 207)))
        self.assertEqual(response.data[0].author_role, "LECTURER")
        self.assertEqual(response.data[1].author_role, "STUDENT")
        self.assertEqual(response.data[-1].author_role, "STUDENT")
        self.assertEqual(response.data[1].author_name, "student20@example.test")
        with self.assertRaises(ForbiddenError):
            await content_service.list_course_discussions(self.db(), 2, 20, "STUDENT")

    async def test_course_lookup_applies_id_in_sql_and_preserves_membership(self):
        row = await PortalRepository(self.db()).get_course(1, 20, "STUDENT")
        self.assertEqual(row[0].course_id, 1)
        self.assertIn("lms_courses.course_id = ?", self.queries[-1])
        self.assertIsNone(await PortalRepository(self.db()).get_course(2, 20, "STUDENT"))
        self.assertIsNone(await PortalRepository(self.db()).get_course(1, 99, "STUDENT"))
