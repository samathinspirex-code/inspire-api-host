"""Admin home totals: five bounded reads, without loading rosters or full courses."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.cms.models import Program
from app.modules.lms.models import (
    AttendanceRecord, ClassStudent, CourseEnrollment, LecturerProfile, LmsClass,
    LmsCourse, LmsLearningItem, LmsModule, OnlineMeeting, StudentProfile,
)
from app.modules.lms.schemas.dashboard import (
    AdminDashboardCourse, AdminDashboardMeeting, AdminDashboardPopularCourse,
    AdminDashboardResponse, ClassPopulationItem, CoursePopulationItem,
    StudentPopulationResponse,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def get_admin_dashboard(db: AsyncSession) -> AdminDashboardResponse:
    now = datetime.now(timezone.utc)
    enrolment_period = now - timedelta(days=30)
    previous_enrolment_period = now - timedelta(days=60)
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
        count(CourseEnrollment, CourseEnrollment.status == "enrolled", CourseEnrollment.enrolled_at >= enrolment_period).label("new_enrolments_30d"),
        count(CourseEnrollment, CourseEnrollment.status == "enrolled", CourseEnrollment.enrolled_at >= previous_enrolment_period, CourseEnrollment.enrolled_at < enrolment_period).label("enrolments_previous_30d"),
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
    popular_courses = (await db.execute(
        select(
            LmsCourse.course_id, LmsCourse.code, LmsCourse.title,
            func.count(CourseEnrollment.student_user_id).label("enrolments"),
        )
        .join(CourseEnrollment, CourseEnrollment.course_id == LmsCourse.course_id)
        .where(CourseEnrollment.status == "enrolled")
        .group_by(LmsCourse.course_id, LmsCourse.code, LmsCourse.title)
        .order_by(func.count(CourseEnrollment.student_user_id).desc(), LmsCourse.title)
        .limit(5)
    )).all()
    return AdminDashboardResponse(
        **totals._mapping,
        attendance_rate=round(attendance.present * 100 / attendance.total, 1) if attendance.total else None,
        attendance_records=attendance.total,
        popular_course=AdminDashboardPopularCourse.model_validate(popular_courses[0]) if popular_courses else None,
        popular_courses=[AdminDashboardPopularCourse.model_validate(row) for row in popular_courses],
        upcoming_meetings=[AdminDashboardMeeting(
            **{**row._mapping, "start_time": _utc(row.start_time), "end_time": _utc(row.end_time)}
        ) for row in meetings],
        recent_courses=[AdminDashboardCourse.model_validate(row) for row in courses],
        generated_at=now,
    )


async def get_student_population(db: AsyncSession) -> StudentPopulationResponse:
    """A compact enrolment view for Super Admin analysis, not a second user registry."""
    now = datetime.now(timezone.utc)
    period_start = now - timedelta(days=30)
    previous_period_start = now - timedelta(days=60)

    total_students = await db.scalar(
        select(func.count()).select_from(StudentProfile).join(User, User.user_id == StudentProfile.user_id)
    ) or 0
    active_course_enrolments = await db.scalar(
        select(func.count()).select_from(CourseEnrollment).where(CourseEnrollment.status == "enrolled")
    ) or 0
    active_class_enrolments = await db.scalar(
        select(func.count()).select_from(ClassStudent)
    ) or 0
    new_enrolments = await db.scalar(
        select(func.count()).select_from(CourseEnrollment).where(
            CourseEnrollment.status == "enrolled", CourseEnrollment.enrolled_at >= period_start,
        )
    ) or 0
    previous_enrolments = await db.scalar(
        select(func.count()).select_from(CourseEnrollment).where(
            CourseEnrollment.status == "enrolled",
            CourseEnrollment.enrolled_at >= previous_period_start,
            CourseEnrollment.enrolled_at < period_start,
        )
    ) or 0

    course_rows = (await db.execute(
        select(
            LmsCourse.course_id, LmsCourse.code, LmsCourse.title,
            func.count(CourseEnrollment.student_user_id).filter(CourseEnrollment.status == "enrolled").label("population"),
            func.count(CourseEnrollment.student_user_id).filter(
                CourseEnrollment.status == "enrolled", CourseEnrollment.enrolled_at >= period_start,
            ).label("new_enrolments_30d"),
        )
        .outerjoin(CourseEnrollment, CourseEnrollment.course_id == LmsCourse.course_id)
        .where(LmsCourse.status != "archived")
        .group_by(LmsCourse.course_id, LmsCourse.code, LmsCourse.title)
        .order_by(func.count(CourseEnrollment.student_user_id).filter(CourseEnrollment.status == "enrolled").desc(), LmsCourse.title)
    )).all()
    class_rows = (await db.execute(
        select(
            LmsClass.class_id, LmsCourse.course_id, LmsCourse.code.label("course_code"),
            LmsCourse.title.label("course_title"), LmsClass.code, LmsClass.name,
            func.count(ClassStudent.student_user_id).label("population"),
            func.count(ClassStudent.student_user_id).filter(
                ClassStudent.assigned_at >= period_start,
            ).label("new_enrolments_30d"),
            LmsClass.start_date, LmsClass.end_date, LmsClass.status,
        )
        .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
        .outerjoin(ClassStudent, ClassStudent.class_id == LmsClass.class_id)
        .where(LmsClass.status != "archived", LmsCourse.status != "archived")
        .group_by(
            LmsClass.class_id, LmsCourse.course_id, LmsCourse.code, LmsCourse.title,
            LmsClass.code, LmsClass.name, LmsClass.start_date, LmsClass.end_date, LmsClass.status,
        )
        .order_by(func.count(ClassStudent.student_user_id).desc(), LmsClass.start_date.desc())
        .limit(100)
    )).all()
    return StudentPopulationResponse(
        total_students=total_students,
        active_course_enrolments=active_course_enrolments,
        active_class_enrolments=active_class_enrolments,
        new_enrolments_30d=new_enrolments,
        enrolments_previous_30d=previous_enrolments,
        course_population=[CoursePopulationItem.model_validate(row) for row in course_rows],
        class_population=[ClassPopulationItem.model_validate(row) for row in class_rows],
        generated_at=now,
    )
