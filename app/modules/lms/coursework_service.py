from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.cms import media_service
from app.modules.cms.models import MediaAsset
from app.modules.lms.models import (
    ClassStudent,
    CourseEnrollment,
    CourseLecturer,
    LmsClass,
    LmsCourseworkAssignment,
    LmsCourseworkSubmission,
)
from app.modules.lms.repository import CourseworkRepository
from app.modules.lms.schemas import (
    CourseworkAssignmentCreate,
    CourseworkAssignmentItem,
    CourseworkAssignmentListResponse,
    CourseworkDraftUpdate,
    CourseworkMarkUpdate,
    CourseworkSubmissionItem,
    CourseworkSubmissionListResponse,
)
from app.modules.lms import notification_service


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def fixed_expiry(started_at: datetime, duration_minutes: int | None, due_at: datetime | None) -> datetime | None:
    """Return the permanent deadline for an attempt. Never recalculate from a later request."""
    expiry = as_utc(due_at)
    if duration_minutes is not None:
        duration_expiry = as_utc(started_at) + timedelta(minutes=duration_minutes)
        expiry = min(expiry, duration_expiry) if expiry else duration_expiry
    return expiry


def remaining_seconds(expires_at: datetime | None, now: datetime | None = None) -> int | None:
    if expires_at is None:
        return None
    seconds = int((as_utc(expires_at) - (as_utc(now) or utc_now())).total_seconds())
    return max(0, seconds)


async def _ensure_lecturer_course(db: AsyncSession, course_id: int, user_id: int) -> None:
    if await db.get(CourseLecturer, (course_id, user_id)) is None:
        raise ForbiddenError("This course is not assigned to your lecturer profile")


async def _ensure_student_target(db: AsyncSession, assignment, user_id: int) -> None:
    enrollment = await db.get(CourseEnrollment, (assignment.course_id, user_id))
    if enrollment is None or enrollment.status != "enrolled":
        raise ForbiddenError("You are not enrolled in this course")
    if assignment.target_type == "class":
        if await db.get(ClassStudent, (assignment.target_id, user_id)) is None:
            raise ForbiddenError("This assignment is not assigned to your class")


def _asset_url(asset: MediaAsset | None) -> str | None:
    if asset is None or asset.status != "ready":
        return None
    return media_service._public_url(asset.object_key)


def _assignment_item(row, submission=None, asset=None, expose_grade: bool = True) -> CourseworkAssignmentItem:
    assignment, course, target_class = row
    return CourseworkAssignmentItem(
        assignment_id=assignment.assignment_id,
        course_id=assignment.course_id,
        course_code=course.code,
        course_title=course.title,
        target_type=assignment.target_type,
        target_id=assignment.target_id,
        target_label=target_class.name if target_class else "Entire course",
        title=assignment.title,
        instructions=assignment.instructions,
        assignment_type=assignment.assignment_type,
        available_from=assignment.available_from,
        due_at=assignment.due_at,
        duration_minutes=assignment.duration_minutes,
        max_marks=assignment.max_marks,
        allow_late=assignment.allow_late,
        grades_released=assignment.grades_released,
        status=assignment.status,
        created_at=assignment.created_at,
        submission_id=submission.submission_id if submission else None,
        submission_status=submission.status if submission else None,
        started_at=submission.started_at if submission else None,
        expires_at=submission.expires_at if submission else None,
        submitted_at=submission.submitted_at if submission else None,
        marks_awarded=submission.marks_awarded if submission and (assignment.grades_released or expose_grade) else None,
        feedback=submission.feedback if submission and (assignment.grades_released or expose_grade) else None,
        answer_text=submission.answer_text if submission else None,
        attachment_asset_id=submission.attachment_asset_id if submission else None,
        attachment_url=_asset_url(asset),
        attachment_name=asset.original_filename if asset else None,
        remaining_seconds=remaining_seconds(submission.expires_at) if submission else None,
    )


async def create_assignment(db: AsyncSession, payload: CourseworkAssignmentCreate, user_id: int):
    await _ensure_lecturer_course(db, payload.course_id, user_id)
    if payload.target_type == "class":
        target = await db.get(LmsClass, payload.target_id)
        if target is None or target.course_id != payload.course_id:
            raise ValidationError("The selected class does not belong to this course")
    row = LmsCourseworkAssignment(**payload.model_dump(), created_by=user_id)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    if row.status == "published":
        await notification_service.notify_assessment_published(db, row, "assignment")
    context = await CourseworkRepository(db).get_assignment_context(row.assignment_id)
    return _assignment_item(context)


async def list_assignments(db: AsyncSession, user_id: int, role: str) -> CourseworkAssignmentListResponse:
    repo = CourseworkRepository(db)
    if role == "LECTURER":
        rows = await repo.list_for_lecturer(user_id)
        return CourseworkAssignmentListResponse(data=[_assignment_item(row) for row in rows])
    if role != "STUDENT":
        raise ForbiddenError("This LMS role cannot access coursework assignments")
    data = []
    for assignment, course, target_class, submission in await repo.list_for_student(user_id):
        timer_finished = submission and submission.expires_at and remaining_seconds(submission.expires_at) == 0
        deadline_finished = (
            submission
            and assignment.assignment_type == "regular"
            and assignment.due_at
            and as_utc(assignment.due_at) <= utc_now()
            and not assignment.allow_late
        )
        if submission and submission.status == "in_progress" and (timer_finished or deadline_finished):
            submission.status = "expired"
            submission.submitted_at = submission.expires_at or as_utc(assignment.due_at) or utc_now()
        asset = await db.get(MediaAsset, submission.attachment_asset_id) if submission and submission.attachment_asset_id else None
        data.append(_assignment_item((assignment, course, target_class), submission, asset, expose_grade=False))
    await db.commit()
    return CourseworkAssignmentListResponse(data=data)


async def _student_context(db: AsyncSession, assignment_id: int, user_id: int):
    context = await CourseworkRepository(db).get_assignment_context(assignment_id)
    if context is None:
        raise NotFoundError("Assignment not found")
    assignment = context[0]
    await _ensure_student_target(db, assignment, user_id)
    if assignment.status != "published":
        raise ForbiddenError("This assignment has not been published")
    return context


async def start_assignment(db: AsyncSession, assignment_id: int, user_id: int):
    context = await _student_context(db, assignment_id, user_id)
    assignment = context[0]
    repo = CourseworkRepository(db)
    submission = await repo.get_submission(assignment_id, user_id, for_update=True)
    if submission is None:
        now = utc_now()
        if assignment.available_from and as_utc(assignment.available_from) > now:
            raise ValidationError("This assignment is not available yet")
        if assignment.due_at and as_utc(assignment.due_at) <= now and not assignment.allow_late:
            raise ValidationError("The deadline for this assignment has passed")
        submission = LmsCourseworkSubmission(
            assignment_id=assignment_id,
            student_user_id=user_id,
            status="in_progress",
            started_at=now,
            expires_at=(
                fixed_expiry(now, assignment.duration_minutes, assignment.due_at)
                if assignment.assignment_type == "timed"
                else None
            ),
        )
        db.add(submission)
        await db.commit()
        await db.refresh(submission)
    elif submission.status == "in_progress" and remaining_seconds(submission.expires_at) == 0:
        submission.status = "expired"
        submission.submitted_at = submission.expires_at or utc_now()
        await db.commit()
    asset = await db.get(MediaAsset, submission.attachment_asset_id) if submission.attachment_asset_id else None
    return _assignment_item(context, submission, asset, expose_grade=False)


async def _active_submission(db: AsyncSession, assignment_id: int, user_id: int):
    context = await _student_context(db, assignment_id, user_id)
    assignment = context[0]
    submission = await CourseworkRepository(db).get_submission(assignment_id, user_id, for_update=True)
    if submission is None:
        raise ValidationError("Start the assignment before saving or submitting")
    if submission.status != "in_progress":
        raise ValidationError("This attempt is already finished")
    if remaining_seconds(submission.expires_at) == 0:
        submission.status = "expired"
        submission.submitted_at = submission.expires_at or utc_now()
        await db.commit()
        raise ValidationError("The assignment timer has expired")
    if (
        assignment.assignment_type == "regular"
        and assignment.due_at
        and as_utc(assignment.due_at) <= utc_now()
        and not assignment.allow_late
    ):
        submission.status = "expired"
        submission.submitted_at = as_utc(assignment.due_at)
        await db.commit()
        raise ValidationError("The deadline for this assignment has passed")
    return context, submission


async def _validate_attachment(db: AsyncSession, asset_id: int | None, user_id: int) -> MediaAsset | None:
    if asset_id is None:
        return None
    asset = await db.get(MediaAsset, asset_id)
    if asset is None or asset.status != "ready":
        raise ValidationError("The attachment is not ready")
    if asset.created_by != user_id or asset.folder != "assignment-submissions":
        raise ForbiddenError("This attachment does not belong to your assignment upload")
    return asset


async def save_draft(db: AsyncSession, assignment_id: int, payload: CourseworkDraftUpdate, user_id: int):
    context, submission = await _active_submission(db, assignment_id, user_id)
    asset = await _validate_attachment(db, payload.attachment_asset_id, user_id)
    submission.answer_text = payload.answer_text
    submission.attachment_asset_id = asset.media_asset_id if asset else None
    await db.commit()
    await db.refresh(submission)
    return _assignment_item(context, submission, asset, expose_grade=False)


async def submit_assignment(db: AsyncSession, assignment_id: int, payload: CourseworkDraftUpdate, user_id: int):
    context, submission = await _active_submission(db, assignment_id, user_id)
    asset = await _validate_attachment(db, payload.attachment_asset_id, user_id)
    answer = (payload.answer_text or "").strip()
    if not answer and asset is None:
        raise ValidationError("Add an answer or attachment before submitting")
    submission.answer_text = answer or None
    submission.attachment_asset_id = asset.media_asset_id if asset else None
    submission.status = "submitted"
    submission.submitted_at = utc_now()
    await db.commit()
    await db.refresh(submission)
    return _assignment_item(context, submission, asset, expose_grade=False)


async def list_submissions(db: AsyncSession, assignment_id: int, user_id: int):
    assignment = await CourseworkRepository(db).get_assignment(assignment_id)
    if assignment is None:
        raise NotFoundError("Assignment not found")
    await _ensure_lecturer_course(db, assignment.course_id, user_id)
    items = []
    for submission, user, profile, asset in await CourseworkRepository(db).list_submissions(assignment_id):
        items.append(CourseworkSubmissionItem(
            submission_id=submission.submission_id,
            assignment_id=submission.assignment_id,
            student_user_id=submission.student_user_id,
            student_name=user.full_name or user.email,
            student_email=user.email,
            student_number=profile.student_number,
            status=submission.status,
            started_at=submission.started_at,
            expires_at=submission.expires_at,
            submitted_at=submission.submitted_at,
            answer_text=submission.answer_text,
            attachment_asset_id=submission.attachment_asset_id,
            attachment_url=_asset_url(asset),
            attachment_name=asset.original_filename if asset else None,
            marks_awarded=submission.marks_awarded,
            feedback=submission.feedback,
            marked_at=submission.marked_at,
        ))
    return CourseworkSubmissionListResponse(data=items)


async def mark_submission(db: AsyncSession, submission_id: int, payload: CourseworkMarkUpdate, user_id: int):
    context = await CourseworkRepository(db).get_submission_context(submission_id)
    if context is None:
        raise NotFoundError("Submission not found")
    submission, assignment = context
    await _ensure_lecturer_course(db, assignment.course_id, user_id)
    if Decimal(payload.marks_awarded) > Decimal(assignment.max_marks):
        raise ValidationError(f"Marks cannot exceed {assignment.max_marks}")
    if submission.status not in {"submitted", "expired", "reviewed", "returned"}:
        raise ValidationError("The student has not finished this attempt")
    submission.marks_awarded = payload.marks_awarded
    submission.feedback = payload.feedback
    submission.marked_by = user_id
    submission.marked_at = utc_now()
    submission.status = "reviewed"
    await db.commit()
    return await list_submissions(db, assignment.assignment_id, user_id)


async def complete_student_upload(db: AsyncSession, asset_id: int, user_id: int):
    asset = await db.get(MediaAsset, asset_id)
    if asset is None:
        raise NotFoundError("Media asset not found")
    if asset.created_by != user_id or asset.folder != "assignment-submissions":
        raise ForbiddenError("This upload does not belong to your account")
    return await media_service.complete_upload(db, asset_id)
