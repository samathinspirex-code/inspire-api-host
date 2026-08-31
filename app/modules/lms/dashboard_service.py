"""Admin home totals: four bounded reads, without loading rosters or full courses."""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.cms.models import Program
from app.modules.lms.models import (
    AttendanceRecord, LecturerProfile, LmsClass, LmsCourse, LmsLearningItem,
    LmsModule, OnlineMeeting, StudentProfile,
)
from app.modules.lms.schemas.dashboard import (
    AdminDashboardCourse, AdminDashboardMeeting, AdminDashboardResponse,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def get_admin_dashboard(db: AsyncSession) -> AdminDashboardResponse:
    now = datetime.now(timezone.utc)
    # Ongoing sessions are included, cancelled/completed/past sessions are not.
    upcoming = (OnlineMeeting.status == "scheduled", OnlineMeeting.end_time > now)

    def count(model, *filters):
        return select(func.count()).select_from(model).where(*filters).scalar_subquery()

    def people_count(model):
        # Same registry definition as PeopleDirectory and the CMS dashboard.
        return select(func.count()).select_from(model).join(User, User.user_id == model.user_id).scalar_subquery()

    totals = (await db.execute(select(
        people_count(StudentProfile).label("total_students"),
        people_count(LecturerProfile).label("total_lecturers"),
        count(Program).label("total_programmes"),
        count(LmsCourse, LmsCourse.status == "active").label("active_courses"),
        count(LmsClass, LmsClass.status == "active").label("active_classes"),
        select(func.count()).select_from(LmsLearningItem)
        .join(LmsModule, LmsModule.module_id == LmsLearningItem.module_id)
        .join(LmsCourse, LmsCourse.course_id == LmsModule.course_id)
        .where(LmsLearningItem.status == "published").scalar_subquery().label("published_content"),
        count(OnlineMeeting, *upcoming).label("upcoming_classes"),
    ))).one()
    attendance = (await db.execute(select(
        func.count().label("total"),
        func.count().filter(AttendanceRecord.status == "present").label("present"),
    ).select_from(AttendanceRecord))).one()

    meetings = (await db.execute(
        select(OnlineMeeting.meeting_id, OnlineMeeting.title, OnlineMeeting.start_time,
               OnlineMeeting.end_time, LmsClass.name.label("class_name"), LmsCourse.code.label("course_code"))
        .join(LmsClass, LmsClass.class_id == OnlineMeeting.class_id)
        .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
        .where(*upcoming).order_by(OnlineMeeting.start_time, OnlineMeeting.meeting_id).limit(5)
    )).all()
    courses = (await db.execute(
        select(LmsCourse.course_id, LmsCourse.code, LmsCourse.title, LmsCourse.status)
        .order_by(LmsCourse.created_at.desc(), LmsCourse.course_id.desc()).limit(5)
    )).all()
    return AdminDashboardResponse(
        **totals._mapping,
        attendance_rate=round(attendance.present * 100 / attendance.total, 1) if attendance.total else None,
        attendance_records=attendance.total,
        upcoming_meetings=[AdminDashboardMeeting(
            **{**row._mapping, "start_time": _utc(row.start_time), "end_time": _utc(row.end_time)}
        ) for row in meetings],
        recent_courses=[AdminDashboardCourse.model_validate(row) for row in courses],
        generated_at=now,
    )
