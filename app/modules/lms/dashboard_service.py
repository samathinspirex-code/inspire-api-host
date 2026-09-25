"""Admin home totals: five bounded reads, without loading rosters or full courses."""
from datetime import datetime, timedelta, timezone

_real_datetime = datetime
_real_timezone = timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.cms.models import Program
from app.modules.lms.models import (
    AttendanceRecord, AttendanceSession, ClassStudent, CourseEnrollment, LecturerProfile, LmsClass,
    LmsCourse, LmsCourseworkAssignment, LmsCourseworkSubmission, LmsLearningItem, LmsModule, OnlineMeeting, StudentProfile,
)
from app.modules.lms.schemas.dashboard import (
    AdminDashboardActivity, AdminDashboardCourse, AdminDashboardMeeting, AdminDashboardPopularCourse,
    AdminDashboardResponse, ClassPopulationItem, CoursePopulationItem,
    StudentPopulationResponse,
)


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = _real_datetime.fromisoformat(value)
    return value.replace(tzinfo=_real_timezone.utc) if value.tzinfo is None else value.astimezone(_real_timezone.utc)


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
        select(func.count()).select_from(AttendanceRecord).scalar_subquery().label("attendance_records"),
        select(func.count()).select_from(AttendanceRecord).where(AttendanceRecord.status == "present").scalar_subquery().label("attendance_present"),
    ))).one()

    meetings = (await db.execute(
        select(OnlineMeeting.meeting_id, OnlineMeeting.title, OnlineMeeting.start_time,
               OnlineMeeting.end_time, LmsClass.name.label("class_name"), LmsCourse.code.label("course_code"))
        .join(LmsClass, LmsClass.class_id == OnlineMeeting.class_id)
        .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
        .where(*upcoming).order_by(OnlineMeeting.start_time, OnlineMeeting.meeting_id).limit(5)
    )).all()
    courses = (await db.execute(
        select(
            LmsCourse.course_id, LmsCourse.code, LmsCourse.title, LmsCourse.status, LmsCourse.updated_at,
            func.count(CourseEnrollment.student_user_id).filter(CourseEnrollment.status == "enrolled").label("enrolments"),
        )
        .outerjoin(CourseEnrollment, CourseEnrollment.course_id == LmsCourse.course_id)
        .group_by(LmsCourse.course_id, LmsCourse.code, LmsCourse.title, LmsCourse.status, LmsCourse.created_at, LmsCourse.updated_at)
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

    activity_rows = (await db.execute(text("""
        SELECT activity_type, message, occurred_at FROM (
          SELECT 'enrolment' AS activity_type,
                 COALESCE(u.full_name, 'A student') || ' enrolled in ' || c.title AS message,
                 e.enrolled_at AS occurred_at
          FROM lms_course_enrollments e
          JOIN users u ON u.user_id=e.student_user_id
          JOIN lms_courses c ON c.course_id=e.course_id
          WHERE e.status='enrolled' AND e.enrolled_at IS NOT NULL
          UNION ALL
          SELECT 'submission' AS activity_type,
                 COALESCE(u.full_name, 'A student') || ' submitted ' || a.title AS message,
                 s.submitted_at AS occurred_at
          FROM lms_coursework_submissions s
          JOIN users u ON u.user_id=s.student_user_id
          JOIN lms_coursework_assignments a ON a.assignment_id=s.assignment_id
          WHERE s.submitted_at IS NOT NULL
          UNION ALL
          SELECT 'course' AS activity_type,
                 'Course “' || c.title || '” is ' || c.status AS message,
                 c.updated_at AS occurred_at
          FROM lms_courses c
          WHERE c.updated_at IS NOT NULL
          UNION ALL
          SELECT 'lecturer' AS activity_type,
                 COALESCE(u.full_name, 'A lecturer') || ' joined as lecturer' AS message,
                 p.created_at AS occurred_at
          FROM lms_lecturer_profiles p
          JOIN users u ON u.user_id=p.user_id
          WHERE p.created_at IS NOT NULL
          UNION ALL
          SELECT 'attendance' AS activity_type,
                 CAST(COUNT(CASE WHEN r.status='present' THEN 1 END) AS VARCHAR) || ' students attended ' || cl.name AS message,
                 s.synced_at AS occurred_at
          FROM lms_attendance_sessions s
          JOIN lms_classes cl ON cl.class_id=s.class_id
          LEFT JOIN lms_attendance_records r ON r.attendance_session_id=s.attendance_session_id
          WHERE s.synced_at IS NOT NULL
          GROUP BY s.attendance_session_id, s.synced_at, cl.name
        ) feed
        WHERE occurred_at IS NOT NULL
        ORDER BY occurred_at DESC
        LIMIT 6
    """))).mappings().all()
    activity = [AdminDashboardActivity(
        activity_type=row["activity_type"], message=row["message"], occurred_at=_utc(row["occurred_at"]),
    ) for row in activity_rows]

    totals_dict = dict(totals._mapping)
    del totals_dict["attendance_present"]
    del totals_dict["attendance_records"]
    return AdminDashboardResponse(
        **totals_dict,
        attendance_rate=round(totals.attendance_present * 100 / totals.attendance_records, 1) if totals.attendance_records else None,
        attendance_records=totals.attendance_records,
        popular_course=AdminDashboardPopularCourse.model_validate(popular_courses[0]) if popular_courses else None,
        popular_courses=[AdminDashboardPopularCourse.model_validate(row) for row in popular_courses],
        upcoming_meetings=[AdminDashboardMeeting(
            **{**row._mapping, "start_time": _utc(row.start_time), "end_time": _utc(row.end_time)}
        ) for row in meetings],
        recent_courses=[AdminDashboardCourse.model_validate(row) for row in courses],
        recent_activity=activity[:6],
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
