import csv
import io
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

_real_datetime = datetime

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.lms.models import (
    AttendanceRecord,
    AttendanceSession,
    ClassLecturer,
    ClassStudent,
    CourseEnrollment,
    LmsClass,
    LmsCourse,
    LmsCourseworkAssignment,
    LmsCourseworkSubmission,
    LmsExam,
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
from app.modules.lms.repository.meeting import MEETING_AUDIENCE


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
        stmt = (
            stmt.join(LmsClass, LmsClass.course_id == LmsCourse.course_id)
            .join(ClassLecturer, ClassLecturer.class_id == LmsClass.class_id)
            .where(
                ClassLecturer.lecturer_user_id == user_id,
                LmsClass.status != "cancelled",
            )
            .distinct()
        )
    elif role == "STUDENT":
        stmt = (
            stmt.join(LmsClass, LmsClass.course_id == LmsCourse.course_id)
            .join(ClassStudent, ClassStudent.class_id == LmsClass.class_id)
            .join(
                CourseEnrollment,
                and_(
                    CourseEnrollment.course_id == LmsCourse.course_id,
                    CourseEnrollment.student_user_id == user_id,
                ),
            )
            .where(
                ClassStudent.student_user_id == user_id,
                CourseEnrollment.status == "enrolled",
                LmsClass.status != "cancelled",
            )
            .distinct()
        )
    return list((await db.scalars(stmt)).all())


def _roster_filter(course_column, student_column, roster_by_course: dict[int, set[int]]):
    conditions = [
        and_(course_column == course_id, student_column.in_(student_ids))
        for course_id, student_ids in roster_by_course.items()
        if student_ids
    ]
    return or_(*conditions) if conditions else False


async def get_dashboard(
    db: AsyncSession, user_id: int, role: str,
    program_id: int | None = None, class_id: int | None = None,
) -> AnalyticsDashboardResponse:
    courses = await _scope_courses(db, user_id, role)
    if program_id is not None:
        courses = [course for course in courses if course.program_id == program_id]
    scoped_class_ids: set[int] | None = None
    if role == "LECTURER":
        scoped_class_ids = set((await db.scalars(
            select(ClassLecturer.class_id)
            .join(LmsClass, LmsClass.class_id == ClassLecturer.class_id)
            .where(
                ClassLecturer.lecturer_user_id == user_id,
                LmsClass.status != "cancelled",
            )
        )).all())
    elif role == "STUDENT":
        scoped_class_ids = set((await db.scalars(
            select(ClassStudent.class_id)
            .join(LmsClass, LmsClass.class_id == ClassStudent.class_id)
            .join(
                CourseEnrollment,
                and_(
                    CourseEnrollment.course_id == LmsClass.course_id,
                    CourseEnrollment.student_user_id == user_id,
                ),
            )
            .where(
                ClassStudent.student_user_id == user_id,
                CourseEnrollment.status == "enrolled",
                LmsClass.status != "cancelled",
            )
        )).all())
    roster: set[int] | None = None
    if class_id is not None:
        class_ = await db.get(LmsClass, class_id)
        if class_ is None:
            raise NotFoundError("Class not found")
        if (
            class_.course_id not in {course.course_id for course in courses}
            or (scoped_class_ids is not None and class_id not in scoped_class_ids)
        ):
            raise ForbiddenError("That class is outside this report")
        courses = [course for course in courses if course.course_id == class_.course_id]
        scoped_class_ids = {class_id}
        roster = set((await db.execute(
            select(ClassStudent.student_user_id).where(ClassStudent.class_id == class_id)
        )).scalars().all())
    course_ids = [course.course_id for course in courses]
    now = datetime.now(timezone.utc)
    is_student = role == "STUDENT"

    if not course_ids:
        weeks = _week_buckets()
        return AnalyticsDashboardResponse(
            role=role,
            generated_at=now,
            engagement_score=0,
            engagement_label="Needs attention",
            metrics=[],
            weekly_trend=[AnalyticsTrendPoint(label=w.strftime("%d %b"), activity=0, completions=0) for w in weeks],
            grade_distribution=_distribution([]),
            attendance_distribution=[
                AnalyticsBreakdownItem(label="Present", value=0, percentage=0, tone="green"),
                AnalyticsBreakdownItem(label="Absent", value=0, percentage=0, tone="red"),
            ],
            course_insights=[],
        )

    # 1. Enrolments count & student IDs
    enrolments_count: dict[int, int] = defaultdict(int)
    student_ids: set[int] = set()
    roster_by_course: dict[int, set[int]] = defaultdict(set)
    if scoped_class_ids is not None:
        roster_rows = (await db.execute(
            select(LmsClass.course_id, ClassStudent.student_user_id)
            .join(ClassStudent, ClassStudent.class_id == LmsClass.class_id)
            .where(
                LmsClass.class_id.in_(scoped_class_ids or [-1]),
                LmsClass.course_id.in_(course_ids or [-1]),
            )
        )).all()
        for cid, sid in roster_rows:
            if is_student and sid != user_id:
                continue
            roster_by_course[cid].add(sid)
        for cid, ids in roster_by_course.items():
            enrolments_count[cid] = len(ids)
            student_ids.update(ids)
    else:
        enrolment_stmt = select(
            CourseEnrollment.course_id, CourseEnrollment.student_user_id
        ).where(
            CourseEnrollment.course_id.in_(course_ids),
            CourseEnrollment.status == "enrolled",
        )
        if roster is not None:
            enrolment_stmt = enrolment_stmt.where(CourseEnrollment.student_user_id.in_(roster or [-1]))
        for cid, sid in (await db.execute(enrolment_stmt)).all():
            enrolments_count[cid] += 1
            student_ids.add(sid)

    # 2. Item totals per course
    item_totals = dict((await db.execute(
        select(LmsModule.course_id, func.count(LmsLearningItem.learning_item_id))
        .join(LmsLearningItem, LmsLearningItem.module_id == LmsModule.module_id)
        .where(
            LmsModule.course_id.in_(course_ids),
            LmsModule.status == "active",
            LmsLearningItem.status == "published",
            LmsLearningItem.is_required.is_(True),
        ).group_by(LmsModule.course_id)
    )).all())

    # 3. Sum of completion_percent per course
    sum_progress_by_course: dict[int, float] = defaultdict(float)
    progress_stmt = (
        select(
            LmsModule.course_id,
            func.sum(LmsLearningProgress.completion_percent).label("total_percent"),
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
    elif role == "LECTURER":
        progress_stmt = progress_stmt.where(
            _roster_filter(LmsModule.course_id, LmsLearningProgress.student_user_id, roster_by_course)
        )
    elif roster is not None:
        progress_stmt = progress_stmt.where(LmsLearningProgress.student_user_id.in_(roster or [-1]))
    progress_stmt = progress_stmt.group_by(LmsModule.course_id)
    for cid, total_percent in (await db.execute(progress_stmt)).all():
        if total_percent is not None:
            sum_progress_by_course[cid] = float(total_percent)

    # 4. Attendance summary per course & total
    attendance_by_course: dict[int, tuple[int, int]] = {}  # cid -> (present_count, total_count)
    attendance_stmt = (
        select(
            LmsClass.course_id,
            func.count().filter(AttendanceRecord.status == "present").label("present"),
            func.count().label("total"),
        )
        .join(AttendanceSession, AttendanceSession.attendance_session_id == AttendanceRecord.attendance_session_id)
        .join(LmsClass, LmsClass.class_id == AttendanceSession.class_id)
        .where(LmsClass.course_id.in_(course_ids))
    )
    if is_student:
        attendance_stmt = attendance_stmt.where(
            AttendanceRecord.student_user_id == user_id,
            AttendanceSession.class_id.in_(scoped_class_ids or [-1]),
        )
    elif role == "LECTURER":
        attendance_stmt = attendance_stmt.where(
            AttendanceSession.class_id.in_(scoped_class_ids or [-1])
        )
    elif class_id is not None:
        attendance_stmt = attendance_stmt.where(AttendanceSession.class_id == class_id)
    attendance_stmt = attendance_stmt.group_by(LmsClass.course_id)
    total_present_all = 0
    total_attendance_all = 0
    for cid, present, total in (await db.execute(attendance_stmt)).all():
        p_val = present or 0
        t_val = total or 0
        attendance_by_course[cid] = (p_val, t_val)
        total_present_all += p_val
        total_attendance_all += t_val

    # 5. Grade average per course
    grade_avg_by_course: dict[int, float] = {}
    grade_stmt = (
        select(
            LmsCourseworkAssignment.course_id,
            func.avg(LmsCourseworkSubmission.marks_awarded * 100.0 / LmsCourseworkAssignment.max_marks).label("avg_grade"),
        )
        .select_from(LmsCourseworkSubmission)
        .join(LmsCourseworkAssignment, LmsCourseworkAssignment.assignment_id == LmsCourseworkSubmission.assignment_id)
        .where(
            LmsCourseworkAssignment.course_id.in_(course_ids),
            LmsCourseworkAssignment.grades_released.is_(True),
            LmsCourseworkSubmission.marks_awarded.is_not(None),
            LmsCourseworkAssignment.max_marks > 0,
            ~exists(select(LmsExam.exam_id).where(
                LmsExam.assignment_id == LmsCourseworkAssignment.assignment_id,
                LmsExam.assessment_kind == "practice_test",
            )),
        )
    )
    if is_student:
        grade_stmt = grade_stmt.where(LmsCourseworkSubmission.student_user_id == user_id)
    elif role == "LECTURER":
        grade_stmt = grade_stmt.where(_roster_filter(
            LmsCourseworkAssignment.course_id,
            LmsCourseworkSubmission.student_user_id,
            roster_by_course,
        ))
    elif roster is not None:
        grade_stmt = grade_stmt.where(LmsCourseworkSubmission.student_user_id.in_(roster or [-1]))
    grade_stmt = grade_stmt.group_by(LmsCourseworkAssignment.course_id)
    for cid, avg_grade in (await db.execute(grade_stmt)).all():
        if avg_grade is not None:
            grade_avg_by_course[cid] = round(float(avg_grade), 1)

    # 6. All individual grade percentages for distribution & global average
    all_grades_stmt = (
        select(
            (LmsCourseworkSubmission.marks_awarded * 100.0 / LmsCourseworkAssignment.max_marks).label("pct"),
        )
        .select_from(LmsCourseworkSubmission)
        .join(LmsCourseworkAssignment, LmsCourseworkAssignment.assignment_id == LmsCourseworkSubmission.assignment_id)
        .where(
            LmsCourseworkAssignment.course_id.in_(course_ids),
            LmsCourseworkAssignment.grades_released.is_(True),
            LmsCourseworkSubmission.marks_awarded.is_not(None),
            LmsCourseworkAssignment.max_marks > 0,
            ~exists(select(LmsExam.exam_id).where(
                LmsExam.assignment_id == LmsCourseworkAssignment.assignment_id,
                LmsExam.assessment_kind == "practice_test",
            )),
        )
    )
    if is_student:
        all_grades_stmt = all_grades_stmt.where(LmsCourseworkSubmission.student_user_id == user_id)
    elif role == "LECTURER":
        all_grades_stmt = all_grades_stmt.where(_roster_filter(
            LmsCourseworkAssignment.course_id,
            LmsCourseworkSubmission.student_user_id,
            roster_by_course,
        ))
    elif roster is not None:
        all_grades_stmt = all_grades_stmt.where(LmsCourseworkSubmission.student_user_id.in_(roster or [-1]))
    all_grades = [round(float(pct), 1) for pct in (await db.scalars(all_grades_stmt)).all()]

    insights: list[AnalyticsCourseInsight] = []
    progress_values: list[float] = []
    for course in courses:
        student_count = enrolments_count.get(course.course_id, 0)
        sum_prog = sum_progress_by_course.get(course.course_id, 0.0)
        expected = int(item_totals.get(course.course_id, 0)) * (1 if is_student else student_count)
        progress = round(sum_prog / expected, 1) if expected else None
        p_cnt, t_cnt = attendance_by_course.get(course.course_id, (0, 0))
        attendance = _percentage(p_cnt, t_cnt)
        grade = grade_avg_by_course.get(course.course_id)
        if progress is not None:
            progress_values.append(progress)
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
    attendance_rate = _percentage(total_present_all, total_attendance_all)
    grade_average = _average(all_grades)

    # 7. Recent activity dates & weekly trend points (optimised: fetch only last 8 weeks of timestamps)
    weeks = _week_buckets()
    earliest_date = weeks[0]
    eight_weeks_ago = _real_datetime(earliest_date.year, earliest_date.month, earliest_date.day, tzinfo=timezone.utc)
    recent_cutoff = now - timedelta(days=14)

    progress_activity_dates: list[datetime] = []
    completion_dates: list[datetime] = []
    p_trend_stmt = (
        select(
            LmsLearningProgress.last_activity_at,
            LmsLearningProgress.is_completed,
            LmsLearningProgress.completed_at,
        )
        .select_from(LmsLearningProgress)
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
            or_(
                LmsLearningProgress.last_activity_at >= eight_weeks_ago,
                LmsLearningProgress.completed_at >= eight_weeks_ago,
            ),
        )
    )
    if is_student:
        p_trend_stmt = p_trend_stmt.where(LmsLearningProgress.student_user_id == user_id)
    elif role == "LECTURER":
        p_trend_stmt = p_trend_stmt.where(
            _roster_filter(LmsModule.course_id, LmsLearningProgress.student_user_id, roster_by_course)
        )
    elif roster is not None:
        p_trend_stmt = p_trend_stmt.where(LmsLearningProgress.student_user_id.in_(roster or [-1]))
    for act_at, is_comp, comp_at in (await db.execute(p_trend_stmt)).all():
        if act_at:
            progress_activity_dates.append(_utc(act_at))
        if is_comp and comp_at:
            completion_dates.append(_utc(comp_at))

    submission_dates: list[datetime] = []
    s_trend_stmt = (
        select(LmsCourseworkSubmission.submitted_at)
        .select_from(LmsCourseworkSubmission)
        .join(LmsCourseworkAssignment, LmsCourseworkAssignment.assignment_id == LmsCourseworkSubmission.assignment_id)
        .where(
            LmsCourseworkAssignment.course_id.in_(course_ids),
            LmsCourseworkSubmission.submitted_at >= eight_weeks_ago,
            ~exists(select(LmsExam.exam_id).where(
                LmsExam.assignment_id == LmsCourseworkAssignment.assignment_id,
                LmsExam.assessment_kind == "practice_test",
            )),
        )
    )
    if is_student:
        s_trend_stmt = s_trend_stmt.where(LmsCourseworkSubmission.student_user_id == user_id)
    elif role == "LECTURER":
        s_trend_stmt = s_trend_stmt.where(_roster_filter(
            LmsCourseworkAssignment.course_id,
            LmsCourseworkSubmission.student_user_id,
            roster_by_course,
        ))
    elif roster is not None:
        s_trend_stmt = s_trend_stmt.where(LmsCourseworkSubmission.student_user_id.in_(roster or [-1]))
    for sub_at in (await db.scalars(s_trend_stmt)).all():
        if sub_at:
            submission_dates.append(_utc(sub_at))

    recent_events = sum(recent_cutoff <= dt <= now for dt in progress_activity_dates + submission_dates)
    activity_score = min(100.0, recent_events * (12 if is_student else 2.5))
    weighted = [(overall_progress, 0.40), (attendance_rate, 0.30), (grade_average, 0.20), (activity_score, 0.10)]
    available = [(value, weight) for value, weight in weighted if value is not None]
    engagement = round(sum(value * weight for value, weight in available) / sum(weight for _, weight in available), 1) if available else 0
    engagement_label = "Excellent" if engagement >= 85 else "Strong" if engagement >= 70 else "Developing" if engagement >= 50 else "Needs attention"

    activity_by_week = defaultdict(int)
    completion_by_week = defaultdict(int)
    for value in progress_activity_dates + submission_dates:
        activity_by_week[_week_for(value)] += 1
    for value in completion_dates:
        completion_by_week[_week_for(value)] += 1
    weekly = [AnalyticsTrendPoint(
        label=week.strftime("%d %b"),
        activity=activity_by_week[week],
        completions=completion_by_week[week],
    ) for week in weeks]

    attendance_distribution = []
    for label, status_key, tone in (("Present", "present", "green"), ("Absent", "absent", "red")):
        val = total_present_all if status_key == "present" else (total_attendance_all - total_present_all)
        attendance_distribution.append(AnalyticsBreakdownItem(
            label=label, value=val,
            percentage=round(val * 100 / total_attendance_all, 1) if total_attendance_all else 0,
            tone=tone,
        ))

    if is_student:
        class_ids = list(scoped_class_ids or [])
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
        audience_meetings = select(MEETING_AUDIENCE.c.meeting_id).where(
            MEETING_AUDIENCE.c.class_id.in_(class_ids or [-1])
        )
        upcoming_classes = int(await db.scalar(
            select(func.count()).select_from(OnlineMeeting).where(
                or_(
                    OnlineMeeting.class_id.in_(class_ids or [-1]),
                    OnlineMeeting.meeting_id.in_(audience_meetings),
                ),
                OnlineMeeting.status == "scheduled",
                OnlineMeeting.start_time > now,
            )
        ) or 0)
        metrics = [
            AnalyticsMetric(key="progress", label="Overall progress", value=overall_progress or 0, display_value=_display_percent(overall_progress), hint="Across required course materials", tone="purple"),
            AnalyticsMetric(key="attendance", label="Attendance", value=attendance_rate or 0, display_value=_display_percent(attendance_rate), hint=f"{total_attendance_all} recorded classes", tone="green"),
            AnalyticsMetric(key="grades", label="Grade average", value=grade_average or 0, display_value=_display_percent(grade_average), hint=f"{len(all_grades)} released results", tone="blue"),
            AnalyticsMetric(key="upcoming", label="Coming up", value=assignments_due + upcoming_classes, display_value=str(assignments_due + upcoming_classes), hint=f"{assignments_due} deadlines · {upcoming_classes} classes", tone="amber"),
        ]
    else:
        unmarked_filters = [
            LmsCourseworkAssignment.course_id.in_(course_ids) if course_ids else False,
            LmsCourseworkSubmission.status.in_(["submitted", "expired"]),
            LmsCourseworkSubmission.marks_awarded.is_(None),
        ]
        if roster is not None:
            unmarked_filters.append(LmsCourseworkSubmission.student_user_id.in_(roster or [-1]))
        elif role == "LECTURER":
            unmarked_filters.append(_roster_filter(
                LmsCourseworkAssignment.course_id,
                LmsCourseworkSubmission.student_user_id,
                roster_by_course,
            ))
        unmarked = int(await db.scalar(
            select(func.count()).select_from(LmsCourseworkSubmission)
            .join(LmsCourseworkAssignment, LmsCourseworkAssignment.assignment_id == LmsCourseworkSubmission.assignment_id)
            .where(*unmarked_filters)
        ) or 0)
        metrics = [
            AnalyticsMetric(key="students", label="Enrolled students", value=len(student_ids), display_value=str(len(student_ids)), hint=f"Unique students across {len(scoped_class_ids or [])} assigned classes", tone="purple"),
            AnalyticsMetric(key="progress", label="Average progress", value=overall_progress or 0, display_value=_display_percent(overall_progress), hint="Required content completion", tone="blue"),
            AnalyticsMetric(key="attendance", label="Attendance rate", value=attendance_rate or 0, display_value=_display_percent(attendance_rate), hint=f"{total_attendance_all} recorded attendances", tone="green"),
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


def build_report_csv(report: AnalyticsDashboardResponse) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Inspire LMS academic report"])
    writer.writerow(["Generated", report.generated_at.isoformat()])
    writer.writerow(["Role", report.role])
    writer.writerow(["Engagement score", report.engagement_score, report.engagement_label])
    writer.writerow([])
    writer.writerow(["Metric", "Value", "Detail"])
    for metric in report.metrics:
        writer.writerow([metric.label, metric.display_value, metric.hint])
    writer.writerow([])
    writer.writerow(["Course code", "Course", "Students", "Progress %", "Attendance %", "Grade average %"])
    for course in report.course_insights:
        writer.writerow([
            course.course_code, course.course_title, course.students,
            "" if course.progress is None else course.progress,
            "" if course.attendance is None else course.attendance,
            "" if course.grade_average is None else course.grade_average,
        ])
    writer.writerow([])
    writer.writerow(["Week", "Learning activity", "Completions"])
    for point in report.weekly_trend:
        writer.writerow([point.label, point.activity, point.completions])
    writer.writerow([])
    writer.writerow(["Grade band", "Count", "Percentage"])
    for item in report.grade_distribution:
        writer.writerow([item.label, item.value, item.percentage])
    writer.writerow([])
    writer.writerow(["Attendance", "Count", "Percentage"])
    for item in report.attendance_distribution:
        writer.writerow([item.label, item.value, item.percentage])
    return buffer.getvalue()
