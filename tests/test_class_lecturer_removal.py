"""Class unassignment completes atomically and can safely be retried."""

import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.cms.models import Program
from app.modules.lms import assignment_service
from app.modules.lms.models import ClassLecturer, CourseLecturer, LmsClass, LmsCourse


class LocalSession:
    def __init__(self, engine):
        self.session = Session(engine)
        self.commits = 0

    async def get(self, model, key):
        return self.session.get(model, key)

    async def delete(self, item):
        self.session.delete(item)

    async def flush(self):
        self.session.flush()

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def execute(self, statement):
        return self.session.execute(statement)

    async def commit(self):
        self.session.commit()
        self.commits += 1


class ClassLecturerRemovalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        for model in (User, Program, LmsCourse, LmsClass, ClassLecturer, CourseLecturer):
            model.__table__.create(self.engine)
        self.db = LocalSession(self.engine)
        self.addCleanup(self.db.session.close)
        db = self.db.session
        for user_id in (10, 11):
            db.add(User(user_id=user_id, email=f"user{user_id}@example.test"))
        for course_id in (1, 2):
            db.add(LmsCourse(course_id=course_id, code=f"C{course_id}", title=f"Course {course_id}"))
            db.add(CourseLecturer(course_id=course_id, lecturer_user_id=10))
        db.add(CourseLecturer(course_id=1, lecturer_user_id=11))
        for class_id, course_id in ((1, 1), (2, 1), (3, 2)):
            db.add(LmsClass(class_id=class_id, course_id=course_id, code=f"B{class_id}",
                            name=f"Class {class_id}", start_date=date(2026, 10, 1), end_date=date(2026, 12, 1)))
        db.add_all([ClassLecturer(class_id=1, lecturer_user_id=10),
                    ClassLecturer(class_id=1, lecturer_user_id=11),
                    ClassLecturer(class_id=3, lecturer_user_id=10)])
        db.commit()

    async def test_removes_class_and_unused_course_access_in_one_commit(self):
        await assignment_service.remove_class_lecturer(self.db, 1, 10)
        self.assertIsNone(await self.db.get(ClassLecturer, (1, 10)))
        self.assertIsNone(await self.db.get(CourseLecturer, (1, 10)))
        self.assertIsNotNone(await self.db.get(ClassLecturer, (1, 11)))
        self.assertIsNotNone(await self.db.get(CourseLecturer, (1, 11)))
        self.assertIsNotNone(await self.db.get(ClassLecturer, (3, 10)))
        self.assertIsNotNone(await self.db.get(CourseLecturer, (2, 10)))
        self.assertEqual(self.db.commits, 1)

    async def test_retains_course_access_when_teaching_another_class(self):
        self.db.session.add(ClassLecturer(class_id=2, lecturer_user_id=10))
        self.db.session.commit()
        await assignment_service.remove_class_lecturer(self.db, 1, 10)
        self.assertIsNone(await self.db.get(ClassLecturer, (1, 10)))
        self.assertIsNotNone(await self.db.get(CourseLecturer, (1, 10)))
        self.assertIsNotNone(await self.db.get(ClassLecturer, (2, 10)))
        self.assertEqual(self.db.commits, 1)

    async def test_retry_after_success_is_safe(self):
        await assignment_service.remove_class_lecturer(self.db, 1, 10)
        await assignment_service.remove_class_lecturer(self.db, 1, 10)
        self.assertIsNone(await self.db.get(ClassLecturer, (1, 10)))
        self.assertIsNone(await self.db.get(CourseLecturer, (1, 10)))

    async def test_retry_repairs_cleanup_after_old_partial_removal(self):
        self.db.session.delete(await self.db.get(ClassLecturer, (1, 10)))
        self.db.session.commit()
        await assignment_service.remove_class_lecturer(self.db, 1, 10)
        self.assertIsNone(await self.db.get(CourseLecturer, (1, 10)))

    async def test_cleanup_failure_does_not_commit_class_removal(self):
        async def fail_cleanup(statement):
            raise RuntimeError("Cleanup failed")
        self.db.scalar = fail_cleanup
        with self.assertRaisesRegex(RuntimeError, "Cleanup failed"):
            await assignment_service.remove_class_lecturer(self.db, 1, 10)
        self.assertEqual(self.db.commits, 0)
        self.db.session.rollback()
        self.assertIsNotNone(await self.db.get(ClassLecturer, (1, 10)))
        self.assertIsNotNone(await self.db.get(CourseLecturer, (1, 10)))


if __name__ == "__main__":
    unittest.main()
