from datetime import datetime, timezone

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.auth import service as auth_service
from app.modules.auth.models import AuthenticatorCredential, User
from app.modules.auth.repository.authenticator import AuthenticatorRepository
from app.modules.cms import media_service
from app.modules.cms.models import MediaAsset
from app.modules.cms.schemas import MediaUploadRequest, MediaUploadTicket
from app.modules.lms import content_service
from app.modules.lms.models import (
    AttendanceRecord,
    AttendanceSession,
    ClassLecturer,
    ClassStudent,
    CourseEnrollment,
    CourseLecturer,
    LecturerProfile,
    LmsClass,
    LmsCourse,
    LmsCourseworkAssignment,
    LmsCourseworkSubmission,
    LmsExam,
    LmsExamAttempt,
    LmsExamQuestion,
    LmsLearningItem,
    LmsLearningProgress,
    LmsModule,
    OnlineMeeting,
    StudentProfile,
)
from app.modules.lms.repository import ContentRepository
from app.modules.lms.schemas import (
    MyProfileResponse,
    MyProfileUpdate,
    ProfileStatistics,
    ProfileUpcomingItem,
    RecoveryCodesResponse,
    StudentAcademicActivity,
    StudentAcademicAssessment,
    StudentAcademicAttendance,
    StudentAcademicClass,
    StudentAcademicCourse,
    StudentAcademicProfileResponse,
)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def calculate_profile_completeness(values: list[str | None]) -> int:
    if not values:
        return 0
    completed = sum(1 for value in values if value and str(value).strip())
    return round(completed * 100 / len(values))


async def _student_statistics(
    db: AsyncSession, user_id: int
) -> tuple[ProfileStatistics, list[ProfileUpcomingItem]]:
    now = datetime.now(timezone.utc)
    course_ids = list((await db.scalars(select(CourseEnrollment.course_id).where(
        CourseEnrollment.student_user_id == user_id,
        CourseEnrollment.status == "enrolled",
    ))).all())
    class_ids = list((await db.scalars(select(ClassStudent.class_id).where(
        ClassStudent.student_user_id == user_id
    ))).all())

    attendance = list((await db.scalars(select(AttendanceRecord.status).where(
        AttendanceRecord.student_user_id == user_id
    ))).all())
    attendance_percentage = (
        round(sum(value == "present" for value in attendance) * 100 / len(attendance), 1)
        if attendance else None
    )

    grade_rows = (await db.execute(
        select(LmsCourseworkSubmission.marks_awarded, LmsCourseworkAssignment.max_marks)
        .join(LmsCourseworkAssignment, LmsCourseworkAssignment.assignment_id == LmsCourseworkSubmission.assignment_id)
        .where(
            LmsCourseworkSubmission.student_user_id == user_id,
            LmsCourseworkSubmission.marks_awarded.is_not(None),
            LmsCourseworkAssignment.grades_released.is_(True),
        )
    )).all()
    awarded = sum(float(row.marks_awarded) for row in grade_rows)
    possible = sum(float(row.max_marks) for row in grade_rows)
    grade_average = round(awarded * 100 / possible, 1) if possible else None

    item_ids: list[int] = []
    if course_ids:
        item_ids = list((await db.scalars(
            select(LmsLearningItem.learning_item_id)
            .join(LmsModule, LmsModule.module_id == LmsLearningItem.module_id)
            .where(
                LmsModule.course_id.in_(course_ids),
                LmsModule.status == "active",
                LmsLearningItem.status == "published",
                LmsLearningItem.is_required.is_(True),
            )
        )).all())
    progress_rows = []
    if item_ids:
        progress_rows = list((await db.scalars(select(LmsLearningProgress).where(
            LmsLearningProgress.student_user_id == user_id,
            LmsLearningProgress.learning_item_id.in_(item_ids),
        ))).all())
    course_progress = (
        round(sum(row.completion_percent for row in progress_rows) / len(item_ids), 1)
        if item_ids else None
    )
    completed_materials = sum(row.is_completed for row in progress_rows)

    assignments: list[LmsCourseworkAssignment] = []
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
    if target_conditions:
        assignments = list((await db.scalars(
            select(LmsCourseworkAssignment)
            .where(
                LmsCourseworkAssignment.status == "published",
                LmsCourseworkAssignment.due_at.is_not(None),
                LmsCourseworkAssignment.due_at > now,
                or_(*target_conditions),
            )
            .order_by(LmsCourseworkAssignment.due_at)
        )).all())

    meetings: list[OnlineMeeting] = []
    if class_ids:
        meetings = list((await db.scalars(
            select(OnlineMeeting).where(
                OnlineMeeting.class_id.in_(class_ids),
                OnlineMeeting.status == "scheduled",
                OnlineMeeting.start_time > now,
            ).order_by(OnlineMeeting.start_time)
        )).all())

    course_labels = {}
    if course_ids:
        course_labels = dict((await db.execute(
            select(LmsCourse.course_id, LmsCourse.code).where(LmsCourse.course_id.in_(course_ids))
        )).all())
    class_labels = {}
    if class_ids:
        class_labels = dict((await db.execute(
            select(LmsClass.class_id, LmsClass.code).where(LmsClass.class_id.in_(class_ids))
        )).all())

    upcoming = [
        ProfileUpcomingItem(
            item_type="assignment",
            title=item.title,
            subtitle=course_labels.get(item.course_id, "Course assignment"),
            scheduled_at=item.due_at,
            action_view="assignments",
        ) for item in assignments
    ] + [
        ProfileUpcomingItem(
            item_type="class",
            title=item.title,
            subtitle=class_labels.get(item.class_id, "Online class"),
            scheduled_at=item.start_time,
            action_view="meetings",
        ) for item in meetings
    ]
    upcoming.sort(key=lambda item: item.scheduled_at)
    return ProfileStatistics(
        courses=len(course_ids),
        classes=len(class_ids),
        attendance_percentage=attendance_percentage,
        grade_average=grade_average,
        course_progress=course_progress,
        completed_materials=completed_materials,
        upcoming_deadlines=len(assignments),
        upcoming_classes=len(meetings),
    ), upcoming[:6]


async def _lecturer_statistics(
    db: AsyncSession, user_id: int
) -> tuple[ProfileStatistics, list[ProfileUpcomingItem]]:
    now = datetime.now(timezone.utc)
    course_ids = list((await db.scalars(select(CourseLecturer.course_id).where(
        CourseLecturer.lecturer_user_id == user_id
    ))).all())
    class_ids = list((await db.scalars(select(ClassLecturer.class_id).where(
        ClassLecturer.lecturer_user_id == user_id
    ))).all())
    students = 0
    if course_ids:
        students = int(await db.scalar(select(func.count(func.distinct(CourseEnrollment.student_user_id))).where(
            CourseEnrollment.course_id.in_(course_ids), CourseEnrollment.status == "enrolled"
        )) or 0)
    unmarked = 0
    if course_ids:
        unmarked = int(await db.scalar(
            select(func.count()).select_from(LmsCourseworkSubmission)
            .join(LmsCourseworkAssignment, LmsCourseworkAssignment.assignment_id == LmsCourseworkSubmission.assignment_id)
            .where(
                LmsCourseworkAssignment.course_id.in_(course_ids),
                LmsCourseworkSubmission.status.in_(["submitted", "expired"]),
                LmsCourseworkSubmission.marks_awarded.is_(None),
            )
        ) or 0)
    meetings = list((await db.scalars(
        select(OnlineMeeting).where(
            OnlineMeeting.lecturer_user_id == user_id,
            OnlineMeeting.status == "scheduled",
            OnlineMeeting.start_time > now,
        ).order_by(OnlineMeeting.start_time)
    )).all())
    class_labels = {}
    meeting_class_ids = {item.class_id for item in meetings}
    if meeting_class_ids:
        class_labels = dict((await db.execute(
            select(LmsClass.class_id, LmsClass.code).where(LmsClass.class_id.in_(meeting_class_ids))
        )).all())
    course_progress = None
    if course_ids:
        course_progress_value = await db.scalar(
            select(func.avg(LmsLearningProgress.completion_percent))
            .join(LmsLearningItem, LmsLearningItem.learning_item_id == LmsLearningProgress.learning_item_id)
            .join(LmsModule, LmsModule.module_id == LmsLearningItem.module_id)
            .where(LmsModule.course_id.in_(course_ids))
        )
        course_progress = round(float(course_progress_value), 1) if course_progress_value is not None else None
    upcoming = [ProfileUpcomingItem(
        item_type="class",
        title=item.title,
        subtitle=class_labels.get(item.class_id, "Online class"),
        scheduled_at=item.start_time,
        action_view="meetings",
    ) for item in meetings[:6]]
    return ProfileStatistics(
        courses=len(course_ids),
        classes=len(class_ids),
        course_progress=course_progress,
        upcoming_classes=len(meetings),
        students=students,
        unmarked_submissions=unmarked,
    ), upcoming


async def get_my_profile(db: AsyncSession, user_id: int, role: str) -> MyProfileResponse:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User profile not found")
    credential = await db.get(AuthenticatorCredential, user_id)
    recovery_count = await AuthenticatorRepository(db).count_unused_recovery_codes(user_id)

    if role == "STUDENT":
        profile = await db.get(StudentProfile, user_id)
        if profile is None:
            profile = StudentProfile(user_id=user_id)
            db.add(profile)
            await db.commit()
            await db.refresh(profile)
        statistics, upcoming = await _student_statistics(db, user_id)
        editable_values = [
            user.full_name, profile.profile_image_url, profile.preferred_name, profile.phone,
            profile.bio, profile.address, profile.city, profile.country,
            profile.emergency_contact_name, profile.emergency_contact_phone,
        ]
        return MyProfileResponse(
            user_id=user_id, role=role, email=user.email, full_name=user.full_name or "",
            reference_number=profile.student_number, reference_label="Student number",
            preferred_name=profile.preferred_name, phone=profile.phone,
            profile_image_url=profile.profile_image_url, bio=profile.bio, address=profile.address,
            city=profile.city, country=profile.country,
            emergency_contact_name=profile.emergency_contact_name,
            emergency_contact_phone=profile.emergency_contact_phone,
            profile_completeness=calculate_profile_completeness(editable_values),
            authenticator_enabled=bool(credential and credential.enabled),
            recovery_codes_remaining=recovery_count, statistics=statistics, upcoming=upcoming,
        )

    if role == "LECTURER":
        profile = await db.get(LecturerProfile, user_id)
        if profile is None:
            profile = LecturerProfile(user_id=user_id)
            db.add(profile)
            await db.commit()
            await db.refresh(profile)
        statistics, upcoming = await _lecturer_statistics(db, user_id)
        editable_values = [
            user.full_name, profile.profile_image_url, profile.preferred_name, profile.phone,
            profile.bio, profile.address, profile.city, profile.country, profile.expertise,
        ]
        return MyProfileResponse(
            user_id=user_id, role=role, email=user.email, full_name=user.full_name or "",
            reference_number=profile.staff_number, reference_label="Staff number",
            job_title=profile.job_title, preferred_name=profile.preferred_name,
            phone=profile.phone, profile_image_url=profile.profile_image_url,
            bio=profile.bio, address=profile.address, city=profile.city,
            country=profile.country, expertise=profile.expertise,
            profile_completeness=calculate_profile_completeness(editable_values),
            authenticator_enabled=bool(credential and credential.enabled),
            recovery_codes_remaining=recovery_count, statistics=statistics, upcoming=upcoming,
        )
    raise ValidationError("Profiles are available to students and lecturers")


def _percent(earned, possible) -> float | None:
    if earned is None or not possible or Decimal(possible) <= 0:
        return None
    return round(float(Decimal(earned) * 100 / Decimal(possible)), 1)


async def _ensure_student_profile_access(
    db: AsyncSession, student_user_id: int, viewer_user_id: int, viewer_role: str
) -> None:
    if viewer_role in {"SUPER_ADMIN", "ADMIN"} or (
        viewer_role == "STUDENT" and viewer_user_id == student_user_id
    ):
        return
    if viewer_role != "LECTURER":
        raise ForbiddenError("You cannot view this student profile")
    course_access = await db.scalar(
        select(func.count()).select_from(CourseEnrollment)
        .join(CourseLecturer, CourseLecturer.course_id == CourseEnrollment.course_id)
        .where(
            CourseEnrollment.student_user_id == student_user_id,
            CourseEnrollment.status == "enrolled",
            CourseLecturer.lecturer_user_id == viewer_user_id,
        )
    )
    class_access = await db.scalar(
        select(func.count()).select_from(ClassStudent)
        .join(ClassLecturer, ClassLecturer.class_id == ClassStudent.class_id)
        .where(
            ClassStudent.student_user_id == student_user_id,
            ClassLecturer.lecturer_user_id == viewer_user_id,
        )
    )
    if not course_access and not class_access:
        raise ForbiddenError("You can view profiles only for students you teach")


async def get_student_academic_profile(
    db: AsyncSession, student_user_id: int, viewer_user_id: int, viewer_role: str
) -> StudentAcademicProfileResponse:
    await _ensure_student_profile_access(db, student_user_id, viewer_user_id, viewer_role)
    user = await db.get(User, student_user_id)
    profile = await db.get(StudentProfile, student_user_id)
    if user is None or profile is None:
        raise NotFoundError("Student profile not found")

    course_rows = (await db.execute(
        select(CourseEnrollment, LmsCourse)
        .join(LmsCourse, LmsCourse.course_id == CourseEnrollment.course_id)
        .where(CourseEnrollment.student_user_id == student_user_id,
               CourseEnrollment.status == "enrolled")
        .order_by(LmsCourse.code)
    )).all()
    course_ids = [course.course_id for _enrolment, course in course_rows]
    course_map = {course.course_id: course for _enrolment, course in course_rows}

    class_rows = (await db.execute(
        select(LmsClass, LmsCourse)
        .join(ClassStudent, ClassStudent.class_id == LmsClass.class_id)
        .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
        .where(ClassStudent.student_user_id == student_user_id)
        .order_by(LmsClass.start_date.desc(), LmsClass.code)
    )).all()
    class_ids = [class_.class_id for class_, _course in class_rows]
    class_map = {class_.class_id: class_ for class_, _course in class_rows}

    progress_rows = []
    if course_ids:
        progress_rows = (await db.execute(
            select(LmsLearningItem, LmsModule, LmsLearningProgress)
            .join(LmsModule, LmsModule.module_id == LmsLearningItem.module_id)
            .outerjoin(LmsLearningProgress, and_(
                LmsLearningProgress.learning_item_id == LmsLearningItem.learning_item_id,
                LmsLearningProgress.student_user_id == student_user_id,
            ))
            .where(
                LmsModule.course_id.in_(course_ids),
                LmsModule.status == "active",
                LmsLearningItem.status == "published",
                LmsLearningItem.is_required.is_(True),
                func.lower(func.trim(LmsModule.title)) != "practice test",
            )
            .order_by(LmsModule.course_id, LmsModule.position, LmsLearningItem.position)
        )).all()

    progress_by_course = {course_id: {"total": 0, "completed": 0, "sum": 0.0, "last": None} for course_id in course_ids}
    module_ids = list({module.module_id for _item, module, _progress in progress_rows})
    rules_by_module = await ContentRepository(db).list_access_for_modules(module_ids)
    class_ids_by_course: dict[int, set[int]] = defaultdict(set)
    for class_, course in class_rows:
        class_ids_by_course[course.course_id].add(class_.class_id)
    activities = []
    for item, module, progress in progress_rows:
        released = content_service._student_access(
            rules_by_module.get(module.module_id, []),
            module.course_id,
            student_user_id,
            class_ids_by_course.get(module.course_id, set()),
        )[0]
        if progress is not None:
            activities.append(StudentAcademicActivity(
                learning_item_id=item.learning_item_id, item_title=item.title,
                item_type=item.item_type, course_code=course_map[module.course_id].code,
                section_title=module.title, completion_percent=progress.completion_percent,
                is_completed=progress.is_completed, last_activity_at=progress.last_activity_at,
            ))
        if not released:
            continue
        summary = progress_by_course[module.course_id]
        summary["total"] += 1
        if progress is not None:
            summary["completed"] += int(progress.is_completed)
            summary["sum"] += progress.completion_percent
            if summary["last"] is None or progress.last_activity_at > summary["last"]:
                summary["last"] = progress.last_activity_at
    activities.sort(key=lambda item: item.last_activity_at, reverse=True)

    eligibility = []
    if course_ids:
        eligibility.append(and_(LmsCourseworkAssignment.target_type == "course",
                                LmsCourseworkAssignment.target_id.in_(course_ids)))
    if class_ids:
        eligibility.append(and_(LmsCourseworkAssignment.target_type == "class",
                                LmsCourseworkAssignment.target_id.in_(class_ids)))

    assignment_rows = []
    exam_rows = []
    if eligibility:
        assignment_rows = (await db.execute(
            select(LmsCourseworkAssignment, LmsCourseworkSubmission)
            .outerjoin(LmsCourseworkSubmission, and_(
                LmsCourseworkSubmission.assignment_id == LmsCourseworkAssignment.assignment_id,
                LmsCourseworkSubmission.student_user_id == student_user_id,
            ))
            .where(LmsCourseworkAssignment.status == "published", or_(*eligibility))
            .order_by(LmsCourseworkAssignment.due_at.desc())
        )).all()
        exam_rows = (await db.execute(
            select(LmsExam, LmsExamAttempt)
            .outerjoin(LmsExamAttempt, and_(LmsExamAttempt.exam_id == LmsExam.exam_id,
                                           LmsExamAttempt.student_user_id == student_user_id))
            .where(LmsExam.status == "published", or_(
                and_(LmsExam.target_type == "course", LmsExam.target_id.in_(course_ids or [-1])),
                and_(LmsExam.target_type == "class", LmsExam.target_id.in_(class_ids or [-1])),
            )).order_by(LmsExam.due_at.desc())
        )).all()

    exam_assignment_ids = {exam.assignment_id for exam, _attempt in exam_rows if exam.assessment_kind != "graded_mcq"}
    show_private_marks = viewer_role != "STUDENT"
    assignments = []
    for assignment, submission in assignment_rows:
        if assignment.assignment_id in exam_assignment_ids:
            continue
        visible = show_private_marks or assignment.grades_released
        marks = submission.marks_awarded if submission and visible else None
        assignments.append(StudentAcademicAssessment(
            assessment_id=assignment.assignment_id, kind="assignment", title=assignment.title,
            course_code=course_map[assignment.course_id].code,
            course_title=course_map[assignment.course_id].title,
            class_name=class_map.get(assignment.target_id).name if assignment.target_type == "class" and assignment.target_id in class_map else None,
            status=submission.status if submission else "not_started", marks_awarded=marks,
            max_marks=assignment.max_marks, percentage=_percent(marks, assignment.max_marks),
            feedback=submission.feedback if submission and visible else None,
            submitted_at=submission.submitted_at if submission else None, due_at=assignment.due_at,
            grades_released=assignment.grades_released,
        ))

    question_totals = {}
    exam_ids = [exam.exam_id for exam, _attempt in exam_rows]
    if exam_ids:
        question_totals = dict((await db.execute(
            select(LmsExamQuestion.exam_id, func.sum(LmsExamQuestion.marks))
            .where(LmsExamQuestion.exam_id.in_(exam_ids)).group_by(LmsExamQuestion.exam_id)
        )).all())
    practice_tests, question_papers = [], []
    for exam, attempt in exam_rows:
        if exam.assessment_kind == "graded_mcq":
            continue
        maximum = Decimal(question_totals.get(exam.exam_id) or 0)
        visible = show_private_marks or exam.grades_released or exam.assessment_kind == "practice_test"
        marks = attempt.total_marks if attempt and visible else None
        target = practice_tests if exam.assessment_kind == "practice_test" else question_papers
        target.append(StudentAcademicAssessment(
            assessment_id=exam.exam_id, kind=exam.assessment_kind, title=exam.title,
            course_code=course_map[exam.course_id].code, course_title=course_map[exam.course_id].title,
            class_name=class_map.get(exam.target_id).name if exam.target_type == "class" and exam.target_id in class_map else None,
            status=attempt.status if attempt else "not_started", marks_awarded=marks, max_marks=maximum,
            percentage=_percent(marks, maximum), feedback=attempt.feedback if attempt and visible else None,
            submitted_at=attempt.submitted_at if attempt else None, due_at=exam.due_at,
            grades_released=exam.grades_released,
        ))

    attendance_rows = (await db.execute(
        select(AttendanceRecord, AttendanceSession, OnlineMeeting, LmsClass, LmsCourse)
        .join(AttendanceSession, AttendanceSession.attendance_session_id == AttendanceRecord.attendance_session_id)
        .join(OnlineMeeting, OnlineMeeting.meeting_id == AttendanceSession.meeting_id)
        .join(LmsClass, LmsClass.class_id == AttendanceSession.class_id)
        .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
        .where(AttendanceRecord.student_user_id == student_user_id)
        .order_by(OnlineMeeting.start_time.desc())
    )).all()
    attendance = [StudentAcademicAttendance(
        attendance_record_id=record.attendance_record_id, meeting_title=meeting.title,
        course_code=course.code, class_name=class_.name, started_at=meeting.start_time,
        status=record.status, attendance_percentage=record.attendance_percentage,
        attended_seconds=record.attended_seconds,
    ) for record, _session, meeting, class_, course in attendance_rows]

    graded_assignments = [item for item in assignments if item.percentage is not None]
    graded_practice = [item for item in practice_tests if item.percentage is not None]
    present = sum(item.status == "present" for item in attendance)
    overall_total = sum(value["total"] for value in progress_by_course.values())
    overall_progress = round(sum(value["sum"] for value in progress_by_course.values()) / overall_total, 1) if overall_total else None
    return StudentAcademicProfileResponse(
        user_id=user.user_id, full_name=user.full_name or user.email,
        preferred_name=profile.preferred_name, email=user.email, student_number=profile.student_number,
        profile_image_url=profile.profile_image_url, phone=profile.phone, city=profile.city,
        country=profile.country, bio=profile.bio,
        last_activity_at=activities[0].last_activity_at if activities else None,
        course_progress=overall_progress,
        attendance_percentage=round(present * 100 / len(attendance), 1) if attendance else None,
        assignment_average=round(sum(item.percentage for item in graded_assignments) / len(graded_assignments), 1) if graded_assignments else None,
        practice_average=round(sum(item.percentage for item in graded_practice) / len(graded_practice), 1) if graded_practice else None,
        courses=[StudentAcademicCourse(
            course_id=course.course_id, course_code=course.code, course_title=course.title,
            status=course.status,
            completion_percent=round(progress_by_course[course.course_id]["sum"] / progress_by_course[course.course_id]["total"], 1) if progress_by_course[course.course_id]["total"] else 0,
            completed_items=progress_by_course[course.course_id]["completed"],
            total_items=progress_by_course[course.course_id]["total"],
            last_activity_at=progress_by_course[course.course_id]["last"],
        ) for _enrolment, course in course_rows],
        classes=[StudentAcademicClass(
            class_id=class_.class_id, course_id=course.course_id, class_code=class_.code,
            class_name=class_.name, course_code=course.code, course_title=course.title,
            status=class_.status, start_date=class_.start_date, end_date=class_.end_date,
        ) for class_, course in class_rows],
        assignments=assignments, practice_tests=practice_tests, question_papers=question_papers,
        attendance=attendance, recent_activity=activities[:30],
    )


async def update_my_profile(
    db: AsyncSession, user_id: int, role: str, payload: MyProfileUpdate
) -> MyProfileResponse:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User profile not found")
    user.full_name = payload.full_name.strip()
    if role == "STUDENT":
        profile = await db.get(StudentProfile, user_id)
        if profile is None:
            profile = StudentProfile(user_id=user_id)
            db.add(profile)
        for field in (
            "preferred_name", "phone", "bio", "address", "city", "country",
            "emergency_contact_name", "emergency_contact_phone",
        ):
            setattr(profile, field, _clean(getattr(payload, field)))
    elif role == "LECTURER":
        profile = await db.get(LecturerProfile, user_id)
        if profile is None:
            profile = LecturerProfile(user_id=user_id)
            db.add(profile)
        for field in ("preferred_name", "phone", "bio", "address", "city", "country", "expertise"):
            setattr(profile, field, _clean(getattr(payload, field)))
    else:
        raise ValidationError("Profiles are available to students and lecturers")
    await db.commit()
    return await get_my_profile(db, user_id, role)


async def request_profile_upload(
    db: AsyncSession, payload: MediaUploadRequest, user_id: int
) -> MediaUploadTicket:
    if not payload.content_type.lower().startswith("image/"):
        raise ValidationError("Profile photos must be JPG, PNG, WebP, or GIF images")
    if payload.size_bytes > 5 * 1024 * 1024:
        raise ValidationError("Profile photos must be 5 MB or smaller")
    return await media_service.request_upload(
        db, payload.model_copy(update={"folder": "profile-images"}), user_id
    )


async def complete_profile_upload(
    db: AsyncSession, asset_id: int, user_id: int, role: str
) -> MyProfileResponse:
    asset = await db.get(MediaAsset, asset_id)
    if asset is None or asset.created_by != user_id or asset.folder != "profile-images" or asset.kind != "image":
        raise ValidationError("This upload cannot be used as your profile photo")
    completed = await media_service.complete_upload(db, asset_id)
    if role == "STUDENT":
        profile = await db.get(StudentProfile, user_id)
        if profile is None:
            profile = StudentProfile(user_id=user_id)
            db.add(profile)
    elif role == "LECTURER":
        profile = await db.get(LecturerProfile, user_id)
        if profile is None:
            profile = LecturerProfile(user_id=user_id)
            db.add(profile)
    else:
        profile = None
    if profile is None:
        raise NotFoundError("Profile not found")
    profile.profile_image_url = completed.public_url
    await db.commit()
    return await get_my_profile(db, user_id, role)


async def regenerate_recovery_codes(
    db: AsyncSession, user_id: int, authenticator_code: str
) -> RecoveryCodesResponse:
    codes = await auth_service.regenerate_recovery_codes(db, user_id, authenticator_code)
    return RecoveryCodesResponse(
        recovery_codes=codes,
        message="New recovery codes created. Your previous recovery codes no longer work.",
    )
