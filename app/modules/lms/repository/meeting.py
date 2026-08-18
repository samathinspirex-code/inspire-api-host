from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.lms.models import (
    ClassLecturer,
    ClassStudent,
    LmsClass,
    LmsCourse,
    OnlineMeeting,
)


class MeetingRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_assigned_class(self, class_id: int, lecturer_user_id: int):
        stmt = (
            select(LmsClass, LmsCourse)
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
            .join(ClassLecturer, ClassLecturer.class_id == LmsClass.class_id)
            .where(
                LmsClass.class_id == class_id,
                ClassLecturer.lecturer_user_id == lecturer_user_id,
            )
        )
        return (await self.db.execute(stmt)).one_or_none()

    async def list_student_emails(self, class_id: int) -> list[str]:
        stmt = (
            select(User.email)
            .join(ClassStudent, ClassStudent.student_user_id == User.user_id)
            .where(ClassStudent.class_id == class_id, User.is_active.is_(True))
            .order_by(User.email)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def save(self, data: dict) -> OnlineMeeting:
        item = OnlineMeeting(**data)
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def get_for_lecturer(self, meeting_id: int, lecturer_user_id: int):
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
            .where(
                OnlineMeeting.meeting_id == meeting_id,
                OnlineMeeting.lecturer_user_id == lecturer_user_id,
            )
        )
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
            stmt = stmt.join(ClassStudent, ClassStudent.class_id == OnlineMeeting.class_id).where(
                ClassStudent.student_user_id == user_id
            )
        stmt = stmt.order_by(OnlineMeeting.start_time.desc())
        return list((await self.db.execute(stmt)).all())
