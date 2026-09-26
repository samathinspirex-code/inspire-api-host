from datetime import datetime, timezone

from sqlalchemy import and_, column, func, or_, select, table, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.auth.models import User
from app.modules.lms.models import (
    ClassLecturer,
    ClassStudent,
    CourseEnrollment,
    LmsClass,
    LmsCourse,
    OnlineMeeting,
)

# Created by SQL migration rather than a model, so it is declared lightly here.
MEETING_AUDIENCE = table("lms_meeting_audience_classes", column("meeting_id"), column("class_id"))


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
        return await self.list_student_emails_for_classes([class_id])

    async def list_student_emails_for_classes(self, class_ids: list[int]) -> list[str]:
        stmt = (
            select(User.email).distinct()
            .join(ClassStudent, ClassStudent.student_user_id == User.user_id)
            .where(ClassStudent.class_id.in_(class_ids), User.is_active.is_(True))
            .order_by(User.email)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def audience_class_ids(self, meeting_id: int, fallback_class_id: int) -> list[int]:
        rows = list((await self.db.execute(text("""
            SELECT class_id FROM lms_meeting_audience_classes
            WHERE meeting_id=:meeting_id ORDER BY class_id
        """), {"meeting_id": meeting_id})).scalars().all())
        return rows or [fallback_class_id]

    async def audience_details(self, meeting_ids: list[int]) -> dict[int, tuple[list[int], int]]:
        """Load audience classes and active student totals for many meetings at once."""
        if not meeting_ids:
            return {}
        rows = (await self.db.execute(text("""
            WITH audience AS (
                SELECT ma.meeting_id, ma.class_id
                FROM lms_meeting_audience_classes ma
                WHERE ma.meeting_id = ANY(:meeting_ids)
                UNION
                SELECT m.meeting_id, m.class_id
                FROM lms_online_meetings m
                WHERE m.meeting_id = ANY(:meeting_ids)
                  AND NOT EXISTS (
                      SELECT 1 FROM lms_meeting_audience_classes ma
                      WHERE ma.meeting_id = m.meeting_id
                  )
            )
            SELECT a.meeting_id,
                   ARRAY_AGG(DISTINCT a.class_id ORDER BY a.class_id) AS class_ids,
                   COUNT(DISTINCT u.user_id) AS attendee_count
            FROM audience a
            LEFT JOIN lms_class_students cs ON cs.class_id = a.class_id
            LEFT JOIN users u ON u.user_id = cs.student_user_id AND u.is_active IS TRUE
            GROUP BY a.meeting_id
        """), {"meeting_ids": meeting_ids})).mappings().all()
        return {
            row["meeting_id"]: (list(row["class_ids"]), int(row["attendee_count"]))
            for row in rows
        }

    async def get_user_email(self, user_id: int) -> str | None:
        return await self.db.scalar(select(User.email).where(User.user_id == user_id, User.is_active.is_(True)))

    async def save(self, data: dict, audience_class_ids: list[int] | None = None) -> OnlineMeeting:
        item = OnlineMeeting(**data)
        self.db.add(item)
        await self.db.flush()
        for class_id in dict.fromkeys(audience_class_ids or [item.class_id]):
            await self.db.execute(text("""
                INSERT INTO lms_meeting_audience_classes (meeting_id, class_id)
                VALUES (:meeting_id, :class_id) ON CONFLICT DO NOTHING
            """), {"meeting_id": item.meeting_id, "class_id": class_id})
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def get_for_organiser(self, meeting_id: int, user_id: int, role: str):
        """Administrators can manage any live class; lecturers their own or assigned classes."""
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
            lecturer_class_ids = (
                select(ClassLecturer.class_id).where(ClassLecturer.lecturer_user_id == user_id)
            )
            lecturer_audience_meetings = (
                select(MEETING_AUDIENCE.c.meeting_id).where(
                    MEETING_AUDIENCE.c.class_id.in_(lecturer_class_ids)
                )
            )
            stmt = stmt.where(
                or_(
                    OnlineMeeting.class_id.in_(lecturer_class_ids),
                    OnlineMeeting.meeting_id.in_(lecturer_audience_meetings),
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
            .where(OnlineMeeting.status != "cancelled")
        )
        if role in {"SUPER_ADMIN", "ADMIN"}:
            pass
        elif role == "LECTURER":
            lecturer_class_ids = (
                select(ClassLecturer.class_id).where(ClassLecturer.lecturer_user_id == user_id)
            )
            lecturer_audience_meetings = (
                select(MEETING_AUDIENCE.c.meeting_id).where(
                    MEETING_AUDIENCE.c.class_id.in_(lecturer_class_ids)
                )
            )
            stmt = stmt.where(
                or_(
                    OnlineMeeting.class_id.in_(lecturer_class_ids),
                    OnlineMeeting.meeting_id.in_(lecturer_audience_meetings),
                )
            )
        else:
            now = datetime.now(timezone.utc)
            audience_class = aliased(LmsClass)
            valid_student_classes = (
                select(ClassStudent.class_id)
                .join(audience_class, audience_class.class_id == ClassStudent.class_id)
                .join(
                    CourseEnrollment,
                    and_(
                        CourseEnrollment.course_id == audience_class.course_id,
                        CourseEnrollment.student_user_id == user_id,
                    ),
                )
                .where(
                    ClassStudent.student_user_id == user_id,
                    CourseEnrollment.status == "enrolled",
                    audience_class.status != "cancelled",
                )
            )
            student_audience_meetings = (
                select(MEETING_AUDIENCE.c.meeting_id).where(
                    MEETING_AUDIENCE.c.class_id.in_(valid_student_classes)
                )
            )
            stmt = stmt.where(
                or_(
                    OnlineMeeting.class_id.in_(valid_student_classes),
                    OnlineMeeting.meeting_id.in_(student_audience_meetings),
                ),
                LmsClass.status != "cancelled",
                LmsCourse.status != "archived",
                OnlineMeeting.status == "scheduled",
                OnlineMeeting.end_time >= now,
            )
        stmt = stmt.order_by(OnlineMeeting.start_time.desc())
        return list((await self.db.execute(stmt)).all())
