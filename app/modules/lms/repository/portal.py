from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cms.models import Program
from app.modules.lms.models import (
    ClassLecturer,
    ClassStudent,
    CourseEnrollment,
    CourseLecturer,
    LmsClass,
    LmsCourse,
    LmsModule,
)


class PortalRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _course_relation(self, user_id: int, role: str):
        if role == "LECTURER":
            return CourseLecturer, CourseLecturer.course_id == LmsCourse.course_id, (
                CourseLecturer.lecturer_user_id == user_id
            )
        return CourseEnrollment, CourseEnrollment.course_id == LmsCourse.course_id, (
            CourseEnrollment.student_user_id == user_id
        ) & (CourseEnrollment.status == "enrolled") & (LmsCourse.status == "active")

    def _class_relation(self, user_id: int, role: str):
        if role == "LECTURER":
            return ClassLecturer, ClassLecturer.class_id == LmsClass.class_id, (
                ClassLecturer.lecturer_user_id == user_id
            )
        return ClassStudent, ClassStudent.class_id == LmsClass.class_id, ClassStudent.student_user_id == user_id

    async def list_courses(self, user_id: int, role: str, course_id: int | None = None):
        relation, join_on, access_filter = self._course_relation(user_id, role)
        module_count = (
            select(func.count(LmsModule.module_id))
            .where(
                LmsModule.course_id == LmsCourse.course_id,
                *([LmsModule.status == "active"] if role == "STUDENT" else []),
            )
            .correlate(LmsCourse)
            .scalar_subquery()
        )
        if role == "LECTURER":
            class_count = (
                select(func.count(ClassLecturer.class_id))
                .join(LmsClass, LmsClass.class_id == ClassLecturer.class_id)
                .where(
                    LmsClass.course_id == LmsCourse.course_id,
                    ClassLecturer.lecturer_user_id == user_id,
                )
                .correlate(LmsCourse)
                .scalar_subquery()
            )
            people_count = (
                select(func.count(CourseEnrollment.student_user_id))
                .where(
                    CourseEnrollment.course_id == LmsCourse.course_id,
                    CourseEnrollment.status == "enrolled",
                )
                .correlate(LmsCourse)
                .scalar_subquery()
            )
        else:
            class_count = (
                select(func.count(ClassStudent.class_id))
                .join(LmsClass, LmsClass.class_id == ClassStudent.class_id)
                .where(
                    LmsClass.course_id == LmsCourse.course_id,
                    ClassStudent.student_user_id == user_id,
                )
                .correlate(LmsCourse)
                .scalar_subquery()
            )
            people_count = (
                select(func.count(CourseLecturer.lecturer_user_id))
                .where(CourseLecturer.course_id == LmsCourse.course_id)
                .correlate(LmsCourse)
                .scalar_subquery()
            )
        stmt = (
            select(
                LmsCourse,
                Program.title,
                Program.code,
                module_count,
                class_count,
                people_count,
            )
            .join(Program, Program.program_id == LmsCourse.program_id)
            .join(relation, join_on)
            .where(access_filter)
            .order_by(LmsCourse.title)
        )
        if course_id is not None:
            stmt = stmt.where(LmsCourse.course_id == course_id)
        return list((await self.db.execute(stmt)).all())

    async def get_course(self, course_id: int, user_id: int, role: str):
        rows = await self.list_courses(user_id, role, course_id=course_id)
        return rows[0] if rows else None

    async def list_classes(self, user_id: int, role: str):
        relation, join_on, access_filter = self._class_relation(user_id, role)
        if role == "LECTURER":
            people_count = (
                select(func.count(ClassStudent.student_user_id))
                .where(ClassStudent.class_id == LmsClass.class_id)
                .correlate(LmsClass)
                .scalar_subquery()
            )
        else:
            people_count = (
                select(func.count(ClassLecturer.lecturer_user_id))
                .where(ClassLecturer.class_id == LmsClass.class_id)
                .correlate(LmsClass)
                .scalar_subquery()
            )
        stmt = (
            select(
                LmsClass,
                LmsCourse.code,
                LmsCourse.title,
                Program.title,
                people_count,
            )
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
            .join(Program, Program.program_id == LmsCourse.program_id)
            .join(relation, join_on)
            .where(access_filter)
            .order_by(LmsClass.start_date.desc(), LmsClass.name)
        )
        return list((await self.db.execute(stmt)).all())

    async def get_class(self, class_id: int, user_id: int, role: str):
        rows = await self.list_classes(user_id, role)
        return next((row for row in rows if row[0].class_id == class_id), None)
