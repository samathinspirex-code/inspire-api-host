from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.lms.models import (
    ClassLecturer,
    ClassStudent,
    CourseEnrollment,
    CourseLecturer,
    LecturerProfile,
    LmsClass,
    StudentProfile,
)


class AssignmentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_course_students(self, course_id: int):
        stmt = (
            select(User, StudentProfile, CourseEnrollment)
            .join(StudentProfile, StudentProfile.user_id == User.user_id)
            .join(CourseEnrollment, CourseEnrollment.student_user_id == User.user_id)
            .where(CourseEnrollment.course_id == course_id, CourseEnrollment.status == "enrolled")
            .order_by(User.full_name)
        )
        return [(row[0], row[1], row[2]) for row in (await self.db.execute(stmt)).all()]

    async def get_enrollment(self, course_id: int, student_user_id: int) -> CourseEnrollment | None:
        return await self.db.get(CourseEnrollment, (course_id, student_user_id))

    async def enroll_student(self, course_id: int, student_user_id: int, assigned_by: int) -> CourseEnrollment:
        enrollment = await self.get_enrollment(course_id, student_user_id)
        if enrollment is None:
            enrollment = CourseEnrollment(
                course_id=course_id,
                student_user_id=student_user_id,
                enrolled_by=assigned_by,
            )
            self.db.add(enrollment)
        else:
            enrollment.status = "enrolled"
            enrollment.enrolled_by = assigned_by
            enrollment.enrolled_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(enrollment)
        return enrollment

    async def withdraw_student(self, enrollment: CourseEnrollment) -> None:
        enrollment.status = "withdrawn"
        class_ids = select(LmsClass.class_id).where(LmsClass.course_id == enrollment.course_id)
        await self.db.execute(
            delete(ClassStudent).where(
                ClassStudent.student_user_id == enrollment.student_user_id,
                ClassStudent.class_id.in_(class_ids),
            )
        )
        await self.db.commit()

    async def list_course_lecturers(self, course_id: int):
        stmt = (
            select(User, LecturerProfile, CourseLecturer)
            .join(LecturerProfile, LecturerProfile.user_id == User.user_id)
            .join(CourseLecturer, CourseLecturer.lecturer_user_id == User.user_id)
            .where(CourseLecturer.course_id == course_id)
            .order_by(User.full_name)
        )
        return [(row[0], row[1], row[2]) for row in (await self.db.execute(stmt)).all()]

    async def get_course_lecturer(self, course_id: int, lecturer_user_id: int) -> CourseLecturer | None:
        return await self.db.get(CourseLecturer, (course_id, lecturer_user_id))

    async def assign_course_lecturer(self, course_id: int, lecturer_user_id: int, assigned_by: int) -> CourseLecturer:
        item = CourseLecturer(course_id=course_id, lecturer_user_id=lecturer_user_id, assigned_by=assigned_by)
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def remove_course_lecturer(self, item: CourseLecturer) -> None:
        class_ids = select(LmsClass.class_id).where(LmsClass.course_id == item.course_id)
        await self.db.execute(
            delete(ClassLecturer).where(
                ClassLecturer.lecturer_user_id == item.lecturer_user_id,
                ClassLecturer.class_id.in_(class_ids),
            )
        )
        await self.db.delete(item)
        await self.db.commit()

    async def list_class_students(self, class_id: int):
        stmt = (
            select(User, StudentProfile, ClassStudent)
            .join(StudentProfile, StudentProfile.user_id == User.user_id)
            .join(ClassStudent, ClassStudent.student_user_id == User.user_id)
            .where(ClassStudent.class_id == class_id)
            .order_by(User.full_name)
        )
        return [(row[0], row[1], row[2]) for row in (await self.db.execute(stmt)).all()]

    async def count_class_students(self, class_id: int) -> int:
        stmt = select(func.count()).select_from(ClassStudent).where(ClassStudent.class_id == class_id)
        return (await self.db.execute(stmt)).scalar_one()

    async def get_class_student(self, class_id: int, student_user_id: int) -> ClassStudent | None:
        return await self.db.get(ClassStudent, (class_id, student_user_id))

    async def assign_class_student(self, class_id: int, student_user_id: int, assigned_by: int) -> ClassStudent:
        item = ClassStudent(class_id=class_id, student_user_id=student_user_id, assigned_by=assigned_by)
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def list_class_lecturers(self, class_id: int):
        stmt = (
            select(User, LecturerProfile, ClassLecturer)
            .join(LecturerProfile, LecturerProfile.user_id == User.user_id)
            .join(ClassLecturer, ClassLecturer.lecturer_user_id == User.user_id)
            .where(ClassLecturer.class_id == class_id)
            .order_by(User.full_name)
        )
        return [(row[0], row[1], row[2]) for row in (await self.db.execute(stmt)).all()]

    async def get_class_lecturer(self, class_id: int, lecturer_user_id: int) -> ClassLecturer | None:
        return await self.db.get(ClassLecturer, (class_id, lecturer_user_id))

    async def assign_class_lecturer(self, class_id: int, lecturer_user_id: int, assigned_by: int) -> ClassLecturer:
        item = ClassLecturer(class_id=class_id, lecturer_user_id=lecturer_user_id, assigned_by=assigned_by)
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def remove(self, item) -> None:
        await self.db.delete(item)
        await self.db.commit()
