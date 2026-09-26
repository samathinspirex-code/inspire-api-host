from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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
        ) & (CourseEnrollment.status == "enrolled") & (LmsCourse.status != "archived")

    def _class_relation(self, user_id: int, role: str):
        if role == "LECTURER":
            return ClassLecturer, ClassLecturer.class_id == LmsClass.class_id, (
                ClassLecturer.lecturer_user_id == user_id
            )
        return ClassStudent, ClassStudent.class_id == LmsClass.class_id, ClassStudent.student_user_id == user_id

    async def list_courses(self, user_id: int, role: str, course_id: int | None = None):
        is_manager = role in {"SUPER_ADMIN", "ADMIN"}
        relation, join_on, access_filter = (None, None, None) if is_manager else self._course_relation(user_id, role)
        class_course = aliased(LmsCourse)
        module_count = (
            select(func.count(LmsModule.module_id))
            .where(
                LmsModule.course_id == LmsCourse.course_id,
                func.lower(func.trim(LmsModule.title)) != "practice test",
                *([LmsModule.status == "active"] if role == "STUDENT" else []),
            )
            .correlate(LmsCourse)
            .scalar_subquery()
        )
        if role in {"LECTURER", "SUPER_ADMIN", "ADMIN"}:
            class_count = (
                select(func.count(ClassLecturer.class_id))
                .join(LmsClass, LmsClass.class_id == ClassLecturer.class_id)
                .join(class_course, class_course.course_id == LmsClass.course_id)
                .where(
                    (LmsClass.course_id == LmsCourse.course_id) | (class_course.source_master_course_id == LmsCourse.course_id),
                    ClassLecturer.lecturer_user_id == user_id,
                )
                .correlate(LmsCourse)
                .scalar_subquery()
            )
            if role in {"SUPER_ADMIN", "ADMIN"}:
                class_count = (
                    select(func.count(LmsClass.class_id))
                    .join(class_course, class_course.course_id == LmsClass.course_id)
                    .where((LmsClass.course_id == LmsCourse.course_id) | (class_course.source_master_course_id == LmsCourse.course_id))
                    .correlate(LmsCourse)
                    .scalar_subquery()
                )
            people_count = (
                select(func.count(func.distinct(CourseEnrollment.student_user_id)))
                .join(class_course, class_course.course_id == CourseEnrollment.course_id)
                .where(
                    (CourseEnrollment.course_id == LmsCourse.course_id) | (class_course.source_master_course_id == LmsCourse.course_id),
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
            .outerjoin(Program, Program.program_id == LmsCourse.program_id)
            .order_by(LmsCourse.title)
        )
        if course_id is None and role != "STUDENT":
            stmt = stmt.where(LmsCourse.is_class_copy.is_(False), LmsCourse.status != "archived")
        if not is_manager:
            if role == "LECTURER":
                has_class_assignment = (
                    select(ClassLecturer.class_id)
                    .join(LmsClass, LmsClass.class_id == ClassLecturer.class_id)
                    .join(class_course, class_course.course_id == LmsClass.course_id)
                    .where(
                        (LmsClass.course_id == LmsCourse.course_id) | (class_course.source_master_course_id == LmsCourse.course_id),
                        ClassLecturer.lecturer_user_id == user_id,
                        LmsClass.status != "cancelled",
                    )
                    .exists()
                )
                # A reusable Course page is visible only while the lecturer
                # teaches at least one class that uses it.
                stmt = stmt.where(has_class_assignment)
            elif role == "STUDENT":
                stmt = stmt.join(relation, join_on).where(access_filter)
                class_access = (
                    select(ClassStudent.class_id)
                    .join(LmsClass, LmsClass.class_id == ClassStudent.class_id)
                    .where(
                        ClassStudent.student_user_id == user_id,
                        LmsClass.course_id == LmsCourse.course_id,
                        LmsClass.status != "cancelled",
                    )
                    .exists()
                )
                stmt = stmt.where(class_access)
        if course_id is not None:
            stmt = stmt.where(LmsCourse.course_id == course_id)
        return list((await self.db.execute(stmt)).all())

    async def get_course(self, course_id: int, user_id: int, role: str):
        rows = await self.list_courses(user_id, role, course_id=course_id)
        return rows[0] if rows else None

    async def list_classes(self, user_id: int, role: str):
        is_manager = role in {"SUPER_ADMIN", "ADMIN"}
        relation, join_on, access_filter = (None, None, None) if is_manager else self._class_relation(user_id, role)
        if role in {"LECTURER", "SUPER_ADMIN", "ADMIN"}:
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
                LmsCourse.cover_image_url,
                Program.title,
                people_count,
                LmsCourse.is_orientation,
            )
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
            .outerjoin(Program, Program.program_id == LmsCourse.program_id)
            .order_by(LmsClass.start_date.desc(), LmsClass.name)
        )
        stmt = stmt.where(LmsClass.status != "cancelled")
        if not is_manager:
            stmt = stmt.join(relation, join_on).where(access_filter)
            if role == "STUDENT":
                active_enrollment = (
                    select(CourseEnrollment.course_id)
                    .where(
                        CourseEnrollment.course_id == LmsClass.course_id,
                        CourseEnrollment.student_user_id == user_id,
                        CourseEnrollment.status == "enrolled",
                    )
                    .exists()
                )
                stmt = stmt.where(active_enrollment)
        return list((await self.db.execute(stmt)).all())

    async def get_class(self, class_id: int, user_id: int, role: str):
        rows = await self.list_classes(user_id, role)
        return next((row for row in rows if row[0].class_id == class_id), None)
