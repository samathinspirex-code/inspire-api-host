from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.lms.models import (
    AttendanceRecord,
    AttendanceSession,
    ClassStudent,
    CourseEnrollment,
    CourseLecturer,
    LmsClass,
    LmsCourse,
    LmsCourseworkAssignment,
    LmsCourseworkSubmission,
    LmsLearningItem,
    LmsLearningProgress,
    LmsModule,
    OnlineMeeting,
)
from app.modules.lms.schemas import (
    AnalyticsBreakdownItem,
    AnalyticsCourseInsight,
    AnalyticsDashboardResponse,
    AnalyticsMetric,
    AnalyticsTrendPoint,
)


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _percentage(part: float, total: float) -> float | None:
    return round(part * 100 / total, 1) if total else None


def _display_percent(value: float | None) -> str:
    return "—" if value is None else f"{round(value)}%"


def _week_buckets() -> list[date]:
    today = datetime.now(timezone.utc).date()
    current = today - timedelta(days=today.weekday())
    return [current - timedelta(weeks=index) for index in reversed(range(8))]


def _week_for(value: datetime) -> date:
    day = value.date()
    return day - timedelta(days=day.weekday())


def _distribution(values: list[float]) -> list[AnalyticsBreakdownItem]:
    bands = [
        ("Excellent", 85, 101, "green"),
        ("Good", 70, 85, "purple"),
        ("Developing", 50, 70, "amber"),
        ("Needs support", 0, 50, "red"),
    ]
    total = len(values)
    return [AnalyticsBreakdownItem(
        label=label,
        value=sum(lower <= value < upper for value in values),
        percentage=round(sum(lower <= value < upper for value in values) * 100 / total, 1) if total else 0,
        tone=tone,
    ) for label, lower, upper, tone in bands]


async def _scope_courses(db: AsyncSession, user_id: int, role: str) -> list[LmsCourse]:
    stmt = select(LmsCourse).where(LmsCourse.status != "archived").order_by(LmsCourse.code)
    if role == "LECTURER":
        stmt = stmt.join(CourseLecturer, CourseLecturer.course_id == LmsCourse.course_id).where(
            CourseLecturer.lecturer_user_id == user_id
        )
    elif role == "STUDENT":
        stmt = stmt.join(CourseEnrollment, CourseEnrollment.course_id == LmsCourse.course_id).where(
            CourseEnrollment.student_user_id == user_id,
            CourseEnrollment.status == "enrolled",
        )
    return list((await db.scalars(stmt)).all())


async def get_dashboard(
    db: AsyncSession, user_id: int, role: str
) -> AnalyticsDashboardResponse:
    courses = await _scope_courses(db, user_id, role)
    course_ids = [course.course_id for course in courses]
    now = datetime.now(timezone.utc)
    is_student = role == "STUDENT"

    enrolments: dict[int, set[int]] = defaultdict(set)
    if course_ids:
        for course_id, student_id in (await db.execute(
            select(CourseEnrollment.course_id, CourseEnrollment.student_user_id).where(
                CourseEnrollment.course_id.in_(course_ids), CourseEnrollment.status == "enrolled"
            )
        )).all():
            enrolments[course_id].add(student_id)

    item_totals = dict((await db.execute(
        select(LmsModule.course_id, func.count(LmsLearningItem.learning_item_id))
        .join(LmsLearningItem, LmsLearningItem.module_id == LmsModule.module_id)
        .where(
            LmsModule.course_id.in_(course_ids) if course_ids else False,
            LmsModule.status == "active",
            LmsLearningItem.status == "published",
            LmsLearningItem.is_required.is_(True),
        ).group_by(LmsModule.course_id)
    )).all()) if course_ids else {}

    progress_by_course: dict[int, list[float]] = defaultdict(list)
    completion_dates: list[datetime] = []
    progress_dates: list[datetime] = []
    if course_ids:
        progress_stmt = (
            select(
                LmsModule.course_id,
                LmsLearningProgress.completion_percent,
                LmsLearningProgress.is_completed,
                LmsLearningProgress.completed_at,
                LmsLearningProgress.last_activity_at,
            )
            .join(LmsLearningItem, LmsLearningItem.learning_item_id == LmsLearningProgress.learning_item_id)
            .join(LmsModule, LmsModule.module_id == LmsLearningItem.module_id)
            .join(CourseEnrollment, (CourseEnrollment.course_id == LmsModule.course_id)
                  & (CourseEnrollment.student_user_id == LmsLearningProgress.student_user_id)
                  & (CourseEnrollment.status == "enrolled"))
            .where(
                LmsModule.course_id.in_(course_ids),
                LmsModule.status == "active",
                LmsLearningItem.status == "published",
                LmsLearningItem.is_required.is_(True),
            )
        )
        if is_student:
            progress_stmt = progress_stmt.where(LmsLearningProgress.student_user_id == user_id)
        for course_id, percent, completed, completed_at, activity_at in (await db.execute(progress_stmt)).all():
            progress_by_course[course_id].append(float(percent))
            if activity_at:
                progress_dates.append(_utc(activity_at))
            if completed and completed_at:
                completion_dates.append(_utc(completed_at))

    attendance_by_course: dict[int, list[str]] = defaultdict(list)
    if course_ids:
        attendance_stmt = (
            select(LmsClass.course_id, AttendanceRecord.status)
            .join(AttendanceSession, AttendanceSession.attendance_session_id == AttendanceRecord.attendance_session_id)
            .join(LmsClass, LmsClass.class_id == AttendanceSession.class_id)
            .where(LmsClass.course_id.in_(course_ids))
        )
        if is_student:
            attendance_stmt = attendance_stmt.where(AttendanceRecord.student_user_id == user_id)
        for course_id, status in (await db.execute(attendance_stmt)).all():
            attendance_by_course[course_id].append(status)

    grades_by_course: dict[int, list[float]] = defaultdict(list)
    submission_dates: list[datetime] = []
    if course_ids:
        grade_stmt = (
            select(
                LmsCourseworkAssignment.course_id,
                LmsCourseworkSubmission.marks_awarded,
                LmsCourseworkAssignment.max_marks,
                LmsCourseworkSubmission.submitted_at,
            )
            .join(LmsCourseworkAssignment, LmsCourseworkAssignment.assignment_id == LmsCourseworkSubmission.assignment_id)
            .where(
                LmsCourseworkAssignment.course_id.in_(course_ids),
                LmsCourseworkAssignment.grades_released.is_(True),
                LmsCourseworkSubmission.marks_awarded.is_not(None),
            )
        )
        if is_student:
            grade_stmt = grade_stmt.where(LmsCourseworkSubmission.student_user_id == user_id)
        for course_id, marks, maximum, submitted_at in (await db.execute(grade_stmt)).all():
            if float(maximum):
                grades_by_course[course_id].append(round(float(marks) * 100 / float(maximum), 1))
            if submitted_at:
                submission_dates.append(_utc(submitted_at))

    insights: list[AnalyticsCourseInsight] = []
    all_attendance: list[str] = []
    all_grades: list[float] = []
    progress_values: list[float] = []
    for course in courses:
        student_count = len(enrolments[course.course_id])
        progresses = progress_by_course[course.course_id]
        expected = int(item_totals.get(course.course_id, 0)) * (1 if is_student else student_count)
        progress = round(sum(progresses) / expected, 1) if expected else None
        attendance_values = attendance_by_course[course.course_id]
        attendance = _percentage(attendance_values.count("present"), len(attendance_values))
        grades = grades_by_course[course.course_id]
        grade = _average(grades)
        if progress is not None: progress_values.append(progress)
        all_attendance.extend(attendance_values)
        all_grades.extend(grades)
        insights.append(AnalyticsCourseInsight(
            course_id=course.course_id,
            course_code=course.code,
            course_title=course.title,
            students=student_count,
            progress=progress,
            attendance=attendance,
            grade_average=grade,
        ))

    overall_progress = _average(progress_values)
    attendance_rate = _percentage(all_attendance.count("present"), len(all_attendance))
    grade_average = _average(all_grades)
    recent_cutoff = now - timedelta(days=14)
    recent_events = sum(recent_cutoff <= value <= now for value in progress_dates + submission_dates)
    activity_score = min(100.0, recent_events * (12 if is_student else 2.5))
    weighted = [(overall_progress, 0.40), (attendance_rate, 0.30), (grade_average, 0.20), (activity_score, 0.10)]
    available = [(value, weight) for value, weight in weighted if value is not None]
    engagement = round(sum(value * weight for value, weight in available) / sum(weight for _, weight in available), 1) if available else 0
    engagement_label = "Excellent" if engagement >= 85 else "Strong" if engagement >= 70 else "Developing" if engagement >= 50 else "Needs attention"

    weeks = _week_buckets()
    activity_by_week = defaultdict(int)
    completion_by_week = defaultdict(int)
    for value in progress_dates + submission_dates:
        activity_by_week[_week_for(value)] += 1
    for value in completion_dates:
        completion_by_week[_week_for(value)] += 1
    weekly = [AnalyticsTrendPoint(
        label=week.strftime("%d %b"),
        activity=activity_by_week[week],
        completions=completion_by_week[week],
    ) for week in weeks]

    attendance_distribution = []
    for label, status, tone in (("Present", "present", "green"), ("Absent", "absent", "red")):
        value = all_attendance.count(status)
        attendance_distribution.append(AnalyticsBreakdownItem(
            label=label, value=value,
            percentage=round(value * 100 / len(all_attendance), 1) if all_attendance else 0,
            tone=tone,
        ))

    if is_student:
        class_ids = list((await db.scalars(select(ClassStudent.class_id).where(
            ClassStudent.student_user_id == user_id
        ))).all())
        target_conditions = []
        if course_ids:
            target_conditions.append(
                (LmsCourseworkAssignment.target_type == "course")
                & (LmsCourseworkAssignment.target_id.in_(course_ids))
            )
        if class_ids:
            target_conditions.append(
                (LmsCourseworkAssignment.target_type == "class")
                & (LmsCourseworkAssignment.target_id.in_(class_ids))
            )
        assignments_due = int(await db.scalar(
            select(func.count()).select_from(LmsCourseworkAssignment).where(
                LmsCourseworkAssignment.course_id.in_(course_ids) if course_ids else False,
                LmsCourseworkAssignment.status == "published",
                LmsCourseworkAssignment.due_at > now,
                or_(*target_conditions) if target_conditions else False,
            )
        ) or 0)
        upcoming_classes = int(await db.scalar(select(func.count()).select_from(OnlineMeeting).where(
            OnlineMeeting.class_id.in_(class_ids) if class_ids else False,
            OnlineMeeting.status == "scheduled", OnlineMeeting.start_time > now,
        )) or 0)
        metrics = [
            AnalyticsMetric(key="progress", label="Overall progress", value=overall_progress or 0, display_value=_display_percent(overall_progress), hint="Across required course materials", tone="purple"),
            AnalyticsMetric(key="attendance", label="Attendance", value=attendance_rate or 0, display_value=_display_percent(attendance_rate), hint=f"{len(all_attendance)} recorded classes", tone="green"),
            AnalyticsMetric(key="grades", label="Grade average", value=grade_average or 0, display_value=_display_percent(grade_average), hint=f"{len(all_grades)} released results", tone="blue"),
            AnalyticsMetric(key="upcoming", label="Coming up", value=assignments_due + upcoming_classes, display_value=str(assignments_due + upcoming_classes), hint=f"{assignments_due} deadlines · {upcoming_classes} classes", tone="amber"),
        ]
    else:
        student_ids = {student for values in enrolments.values() for student in values}
        unmarked = int(await db.scalar(
            select(func.count()).select_from(LmsCourseworkSubmission)
            .join(LmsCourseworkAssignment, LmsCourseworkAssignment.assignment_id == LmsCourseworkSubmission.assignment_id)
            .where(
                LmsCourseworkAssignment.course_id.in_(course_ids) if course_ids else False,
                LmsCourseworkSubmission.status.in_(["submitted", "expired"]),
                LmsCourseworkSubmission.marks_awarded.is_(None),
            )
        ) or 0)
        metrics = [
            AnalyticsMetric(key="students", label="Enrolled students", value=len(student_ids), display_value=str(len(student_ids)), hint=f"Unique students across {len(courses)} non-archived courses", tone="purple"),
            AnalyticsMetric(key="progress", label="Average progress", value=overall_progress or 0, display_value=_display_percent(overall_progress), hint="Required content completion", tone="blue"),
            AnalyticsMetric(key="attendance", label="Attendance rate", value=attendance_rate or 0, display_value=_display_percent(attendance_rate), hint=f"{len(all_attendance)} recorded attendances", tone="green"),
            AnalyticsMetric(key="marking", label="Awaiting marking", value=unmarked, display_value=str(unmarked), hint="Submitted assignments and exams", tone="amber"),
        ]

    return AnalyticsDashboardResponse(
        role=role,
        generated_at=now,
        engagement_score=engagement,
        engagement_label=engagement_label,
        metrics=metrics,
        weekly_trend=weekly,
        grade_distribution=_distribution(all_grades),
        attendance_distribution=attendance_distribution,
        course_insights=insights,
    )
