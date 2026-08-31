from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.modules.auth import service as auth_service
from app.modules.auth.models import AuthenticatorCredential, User
from app.modules.auth.repository.authenticator import AuthenticatorRepository
from app.modules.cms import media_service
from app.modules.cms.models import MediaAsset
from app.modules.cms.schemas import MediaUploadRequest, MediaUploadTicket
from app.modules.lms.models import (
    AttendanceRecord,
    ClassLecturer,
    ClassStudent,
    CourseEnrollment,
    CourseLecturer,
    LecturerProfile,
    LmsClass,
    LmsCourse,
    LmsCourseworkAssignment,
    LmsCourseworkSubmission,
    LmsLearningItem,
    LmsLearningProgress,
    LmsModule,
    OnlineMeeting,
    StudentProfile,
)
from app.modules.lms.schemas import (
    MyProfileResponse,
    MyProfileUpdate,
    ProfileStatistics,
    ProfileUpcomingItem,
    RecoveryCodesResponse,
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
                LmsModule.status == "published",
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
            raise NotFoundError("Student profile not found")
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
            raise NotFoundError("Lecturer profile not found")
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
            raise NotFoundError("Student profile not found")
        for field in (
            "preferred_name", "phone", "bio", "address", "city", "country",
            "emergency_contact_name", "emergency_contact_phone",
        ):
            setattr(profile, field, _clean(getattr(payload, field)))
    elif role == "LECTURER":
        profile = await db.get(LecturerProfile, user_id)
        if profile is None:
            raise NotFoundError("Lecturer profile not found")
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
    elif role == "LECTURER":
        profile = await db.get(LecturerProfile, user_id)
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
