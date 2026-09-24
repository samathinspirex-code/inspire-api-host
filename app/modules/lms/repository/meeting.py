from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.lms.models import (
    ClassLecturer,
    ClassStudent,
    CourseEnrollment,
    LmsClass,
    LmsCourse,
    OnlineMeeting,
)


class MeetingRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_schedulable_class(self, class_id: int, user_id: int, role: str):
        """Administrators can schedule any class; lecturers only their own."""
        stmt = (
            select(LmsClass, LmsCourse)
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
            .where(LmsClass.class_id == class_id)
        )
        if role not in {"SUPER_ADMIN", "ADMIN"}:
            stmt = stmt.join(
                ClassLecturer,
                and_(
                    ClassLecturer.class_id == LmsClass.class_id,
                    ClassLecturer.lecturer_user_id == user_id,
                ),
            )
        return (await self.db.execute(stmt)).one_or_none()

    async def list_schedulable_classes(self, user_id: int, role: str):
        """Classes the caller may schedule a live class for."""
        student_count = (
            select(func.count(ClassStudent.student_user_id))
            .where(ClassStudent.class_id == LmsClass.class_id)
            .correlate(LmsClass)
            .scalar_subquery()
        )
        stmt = (
            select(LmsClass, LmsCourse, student_count)
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
            .where(LmsClass.status.in_(("planned", "active")))
        )
        if role not in {"SUPER_ADMIN", "ADMIN"}:
            stmt = stmt.join(
                ClassLecturer,
                and_(
                    ClassLecturer.class_id == LmsClass.class_id,
                    ClassLecturer.lecturer_user_id == user_id,
                ),
            )
        return list((await self.db.execute(stmt.order_by(LmsClass.code))).all())

    async def list_student_emails(self, class_id: int) -> list[str]:
        stmt = (
            select(User.email)
            .join(ClassStudent, ClassStudent.student_user_id == User.user_id)
            .where(ClassStudent.class_id == class_id, User.is_active.is_(True))
            .order_by(User.email)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_user_email(self, user_id: int) -> str | None:
        return await self.db.scalar(select(User.email).where(User.user_id == user_id, User.is_active.is_(True)))

    async def save(self, data: dict) -> OnlineMeeting:
        item = OnlineMeeting(**data)
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def get_for_organiser(self, meeting_id: int, user_id: int, role: str):
        """Administrators can manage any live class; lecturers only their own."""
        attendee_count = (
            select(func.count(ClassStudent.student_user_id))
            .where(ClassStudent.class_id == OnlineMeeting.class_id)
            .correlate(OnlineMeeting)
            .scalar_subquery()
        )
        stmt = (
            select(OnlineMeeting, LmsClass, LmsCourse, attendee_count)
            .join(LmsClass, LmsClass.class_id == OnlineMeeting.class_id)
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
            .where(OnlineMeeting.meeting_id == meeting_id)
        )
        if role not in {"SUPER_ADMIN", "ADMIN"}:
            stmt = stmt.where(OnlineMeeting.lecturer_user_id == user_id)
        return (await self.db.execute(stmt)).one_or_none()

    async def update(self, item: OnlineMeeting, data: dict) -> OnlineMeeting:
        for field, value in data.items():
            setattr(item, field, value)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def list_for_user(self, user_id: int, role: str):
        attendee_count = (
            select(func.count(ClassStudent.student_user_id))
            .where(ClassStudent.class_id == OnlineMeeting.class_id)
            .correlate(OnlineMeeting)
            .scalar_subquery()
        )
        stmt = (
            select(OnlineMeeting, LmsClass, LmsCourse, attendee_count)
            .join(LmsClass, LmsClass.class_id == OnlineMeeting.class_id)
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
        )
        if role in {"SUPER_ADMIN", "ADMIN"}:
            pass
        elif role == "LECTURER":
            stmt = stmt.where(OnlineMeeting.lecturer_user_id == user_id)
        else:
            now = datetime.now(timezone.utc)
            stmt = stmt.join(
                ClassStudent,
                and_(
                    ClassStudent.class_id == OnlineMeeting.class_id,
                    ClassStudent.student_user_id == user_id,
                ),
            ).join(
                CourseEnrollment,
                and_(
                    CourseEnrollment.course_id == LmsCourse.course_id,
                    CourseEnrollment.student_user_id == user_id,
                    CourseEnrollment.status == "enrolled",
                ),
            ).where(
                LmsCourse.status == "active",
                LmsClass.status.in_(("planned", "active")),
                OnlineMeeting.status == "scheduled",
                OnlineMeeting.end_time >= now,
            )
        stmt = stmt.order_by(OnlineMeeting.start_time.desc())
        return list((await self.db.execute(stmt)).all())
