from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.cms import media_service
from app.modules.cms.models import MediaAsset
from app.modules.lms.models import (
    ClassStudent,
    CourseEnrollment,
    LmsClass,
    LmsCourseworkAssignment,
    LmsCourseworkSubmission,
    LmsExam,
    LmsExamAttempt,
    LmsExamQuestion,
    LmsNotification,
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
from app.modules.lms.assessment_grading import grading_mode


GRADE_BAND_PERCENT = {"fail": Decimal("0"), "pass": Decimal("50"), "merit": Decimal("65"), "distinction": Decimal("85")}


def grade_band_for_score(marks, max_marks) -> str:
    """Assign a grade band from an assessment score without changing its marks."""
    maximum = Decimal(max_marks)
    if maximum <= 0:
        raise ValueError("Maximum marks must be positive")
    score_percent = Decimal(marks) * Decimal("100") / maximum
    for band in ("distinction", "merit", "pass"):
        if score_percent >= GRADE_BAND_PERCENT[band]:
            return band
    return "fail"


def band_marks(max_marks, band: str) -> Decimal:
    return (Decimal(max_marks) * GRADE_BAND_PERCENT[band] / Decimal("100")).quantize(Decimal("0.01"))


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


def assignment_is_available(available_from: datetime | None, now: datetime | None = None) -> bool:
    return available_from is None or as_utc(available_from) <= (as_utc(now) or utc_now())


def submission_is_late(submission, due_at: datetime | None) -> bool:
    return bool(
        due_at and submission.submitted_at
        and submission.status in {"submitted", "reviewed", "returned"}
        and as_utc(submission.submitted_at) > as_utc(due_at)
    )


def _expire_with_zero(submission: LmsCourseworkSubmission, closed_at: datetime | None = None) -> None:
    submission.status = "expired"
    submission.submitted_at = as_utc(closed_at) or utc_now()
    submission.marks_awarded = Decimal("0")
    submission.grade_band = "fail"
    submission.feedback = "Automatically graded zero because the submission deadline passed."
    submission.marked_at = utc_now()


def _finalize_saved_draft(submission: LmsCourseworkSubmission, closed_at: datetime | None = None) -> None:
    """Submit saved work at the cutoff; only empty attempts receive a zero."""
    if (submission.answer_text or "").strip() or submission.attachment_asset_id:
        submission.status = "submitted"
        submission.submitted_at = as_utc(closed_at) or utc_now()
    else:
        _expire_with_zero(submission, closed_at)


def _can_change_submission(assignment: LmsCourseworkAssignment, submission: LmsCourseworkSubmission, now: datetime) -> bool:
    return (
        assignment.submission_type != "mcq"
        and not assignment.grades_released
        and (assignment.due_at is None or as_utc(assignment.due_at) > now)
        and (submission.expires_at is None or as_utc(submission.expires_at) > now)
    )


async def _ensure_lecturer_course(db: AsyncSession, course_id: int, user_id: int) -> None:
    from app.modules.lms import content_service
    await content_service.ensure_course_manager(db, course_id, user_id)


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


def _assignment_item(
    row, submission=None, asset=None, question_paper=None, expose_grade: bool = True, material=None, exam_id: int | None = None,
) -> CourseworkAssignmentItem:
    assignment, course, target_class = row
    return CourseworkAssignmentItem(
        grading_mode=grading_mode(assignment),
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
        submission_type=assignment.submission_type,
        question_paper_asset_id=assignment.question_paper_asset_id,
        question_paper_url=_asset_url(question_paper),
        question_paper_name=question_paper.original_filename if question_paper else None,
        material_asset_id=assignment.material_asset_id,
        material_url=_asset_url(material),
        material_name=material.original_filename if material else None,
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
        grade_band=(submission.grade_band or grade_band_for_score(submission.marks_awarded, assignment.max_marks))
        if grading_mode(assignment) == "bands" and assignment.submission_type != "mcq" and submission and submission.marks_awarded is not None and (assignment.grades_released or expose_grade) else None,
        feedback=submission.feedback if submission and (assignment.grades_released or expose_grade) else None,
        answer_text=submission.answer_text if submission else None,
        attachment_asset_id=submission.attachment_asset_id if submission else None,
        attachment_url=_asset_url(asset),
        attachment_name=asset.original_filename if asset else None,
        remaining_seconds=remaining_seconds(submission.expires_at) if submission else None,
        exam_id=exam_id,
    )


async def _assignment_material(db: AsyncSession, assignment: LmsCourseworkAssignment) -> MediaAsset | None:
    return await db.get(MediaAsset, assignment.material_asset_id) if assignment.material_asset_id else None


async def _validate_general_material(
    db: AsyncSession, asset_id: int | None, user_id: int, existing_asset_id: int | None = None,
) -> MediaAsset | None:
    if asset_id is None:
        return None
    asset = await db.get(MediaAsset, asset_id)
    if asset is None or asset.status != "ready" or asset.folder != "assignment-materials" or (
        asset.created_by != user_id and asset_id != existing_asset_id
    ):
        raise ValidationError("Choose a completed assignment material uploaded by your account")
    return asset


async def create_assignment(db: AsyncSession, payload: CourseworkAssignmentCreate, user_id: int):
    await _ensure_lecturer_course(db, payload.course_id, user_id)
    if payload.target_type == "class":
        target = await db.get(LmsClass, payload.target_id)
        if target is None or target.course_id != payload.course_id:
            raise ValidationError("The selected class does not belong to this course")
    question_paper = await _validate_assignment_material(db, payload.question_paper_asset_id, user_id)
    material = await _validate_general_material(db, payload.material_asset_id, user_id)
    row = LmsCourseworkAssignment(**payload.model_dump(), created_by=user_id)
    if row.status == "published" and row.available_from is None:
        row.available_from = utc_now()
    db.add(row)
    await db.flush()
    exam_id = None
    if payload.submission_type == "mcq":
        exam = LmsExam(
            assessment_kind="graded_mcq", assignment_id=row.assignment_id, course_id=row.course_id,
            target_type=row.target_type, target_id=row.target_id, title=row.title, instructions=row.instructions,
            available_from=row.available_from, due_at=row.due_at, duration_minutes=row.duration_minutes,
            randomize_questions=True, randomize_options=True, grades_released=False,
            status="draft", created_by=user_id,
        )
        db.add(exam)
        await db.flush()
        exam_id = exam.exam_id
        row.status = "draft"
    await db.commit()
    await db.refresh(row)
    if row.status == "published":
        await notification_service.notify_assessment_published(db, row, "assignment")
    context = await CourseworkRepository(db).get_assignment_context(row.assignment_id)
    return _assignment_item(context, question_paper=question_paper, material=material, exam_id=exam_id)


async def update_assignment(
    db: AsyncSession, assignment_id: int, payload: CourseworkAssignmentCreate, user_id: int,
):
    context = await CourseworkRepository(db).get_assignment_context(assignment_id)
    if context is None:
        raise NotFoundError("Assignment not found")
    assignment = context[0]
    await _ensure_lecturer_course(db, assignment.course_id, user_id)
    if assignment.submission_type == "mcq" and payload.submission_type != "mcq":
        raise ValidationError("A multiple-choice assignment cannot change to another type")
    if payload.course_id != assignment.course_id:
        await _ensure_lecturer_course(db, payload.course_id, user_id)
    submission_count = int(await db.scalar(
        select(func.count()).select_from(LmsCourseworkSubmission).where(
            LmsCourseworkSubmission.assignment_id == assignment_id
        )
    ) or 0)
    protected_fields = ("course_id", "target_type", "target_id", "assignment_type", "submission_type")
    if submission_count and any(getattr(assignment, field) != getattr(payload, field) for field in protected_fields):
        raise ValidationError("Course, audience, and assignment type cannot be changed after students have started")
    if payload.target_type == "class":
        target = await db.get(LmsClass, payload.target_id)
        if target is None or target.course_id != payload.course_id:
            raise ValidationError("The selected class does not belong to this course")
    question_paper = await _validate_assignment_material(
        db, payload.question_paper_asset_id, user_id, assignment.question_paper_asset_id,
    )
    material = await _validate_general_material(
        db, payload.material_asset_id, user_id, assignment.material_asset_id,
    )
    was_published = assignment.status == "published"
    previous_available_from = assignment.available_from
    for field, value in payload.model_dump().items():
        setattr(assignment, field, value)
    if assignment.status == "published" and assignment.available_from is None:
        assignment.available_from = utc_now()
    if assignment.submission_type == "mcq":
        exam = await db.scalar(select(LmsExam).where(
            LmsExam.assignment_id == assignment_id,
            LmsExam.assessment_kind == "graded_mcq",
        ))
        if exam is None:
            raise ValidationError("This multiple-choice assignment has no linked quiz")
        if assignment.status == "published" and exam.status != "published":
            question_count = int(await db.scalar(select(func.count()).select_from(LmsExamQuestion).where(
                LmsExamQuestion.exam_id == exam.exam_id,
            )) or 0)
            if question_count < 1:
                raise ValidationError("Add at least one multiple-choice question before making this assignment active")
        for field in ("course_id", "target_type", "target_id", "title", "instructions",
                      "available_from", "due_at", "duration_minutes", "status"):
            setattr(exam, field, getattr(assignment, field))
    await db.commit()
    await db.refresh(assignment)
    if assignment.status == "published" and (not was_published or assignment.available_from != previous_available_from):
        await notification_service.notify_assessment_published(db, assignment, "assignment")
    context = await CourseworkRepository(db).get_assignment_context(assignment_id)
    return _assignment_item(context, question_paper=question_paper, material=material)


async def delete_assignment(db: AsyncSession, assignment_id: int, user_id: int) -> None:
    context = await CourseworkRepository(db).get_assignment_context(assignment_id)
    if context is None:
        raise NotFoundError("Assignment not found")
    assignment = context[0]
    await _ensure_lecturer_course(db, assignment.course_id, user_id)
    exam_id = await db.scalar(select(LmsExam.exam_id).where(LmsExam.assignment_id == assignment_id))
    notification_keys = [
        LmsNotification.event_key.in_((
            f"assignment:{assignment_id}:published",
            f"exam:{assignment_id}:published",
            f"grade:assignment:{assignment_id}:released",
            f"grade:exam:{assignment_id}:released",
        )),
        LmsNotification.event_key.like(f"assignment:{assignment_id}:due:%"),
        LmsNotification.event_key.like(f"assignment:{assignment_id}:deadline-summary:%"),
        LmsNotification.event_key.like(f"assignment:{assignment_id}:release-reminder:%"),
    ]
    if exam_id is not None:
        notification_keys.append(LmsNotification.event_key.like(f"exam:{exam_id}:due:%"))
    await db.execute(delete(LmsNotification).where(or_(*notification_keys)))
    # The database cascades this deletion to submissions, the linked quiz,
    # its questions, attempts, answers, and their stored grades.
    await db.delete(assignment)
    await db.commit()


async def activate_assignment(db: AsyncSession, assignment_id: int, user_id: int):
    context = await CourseworkRepository(db).get_assignment_context(assignment_id)
    if context is None:
        raise NotFoundError("Assignment not found")
    assignment = context[0]
    await _ensure_lecturer_course(db, assignment.course_id, user_id)
    exam_id = None
    if assignment.submission_type == "mcq":
        exam = await db.scalar(select(LmsExam).where(LmsExam.assignment_id == assignment.assignment_id, LmsExam.assessment_kind == "graded_mcq"))
        question_count = int(await db.scalar(select(func.count()).select_from(LmsExamQuestion).where(LmsExamQuestion.exam_id == exam.exam_id)) or 0) if exam else 0
        if exam is None or question_count < 1:
            raise ValidationError("Add at least one multiple-choice question before making this assignment active")
        exam.status = "published"
        exam_id = exam.exam_id
    if assignment.status != "published":
        assignment.status = "published"
        if assignment.available_from is None:
            assignment.available_from = utc_now()
        await db.commit()
        await db.refresh(assignment)
        await notification_service.notify_assessment_published(db, assignment, "assignment")
    material = await db.get(MediaAsset, assignment.question_paper_asset_id) if assignment.question_paper_asset_id else None
    context = await CourseworkRepository(db).get_assignment_context(assignment_id)
    return _assignment_item(context, question_paper=material, material=await _assignment_material(db, assignment), exam_id=exam_id)


async def list_assignments(
    db: AsyncSession, user_id: int, role: str, course_id: int | None = None, class_id: int | None = None,
) -> CourseworkAssignmentListResponse:
    repo = CourseworkRepository(db)
    def belongs_to_workspace(assignment: LmsCourseworkAssignment) -> bool:
        if class_id is not None:
            return assignment.target_type == "class" and assignment.target_id == class_id
        if course_id is not None:
            return assignment.course_id == course_id and assignment.target_type == "course"
        return True
    if role in {"LECTURER", "ADMIN", "SUPER_ADMIN"}:
        raw_rows = await repo.list_for_lecturer(user_id) if role == "LECTURER" else await repo.list_for_manager()
        rows = [row for row in raw_rows if belongs_to_workspace(row[0])]
        exam_ids = await _graded_exam_ids(db, [row[0].assignment_id for row in rows])
        data = []
        for row in rows:
            material = await db.get(MediaAsset, row[0].question_paper_asset_id) if row[0].question_paper_asset_id else None
            data.append(_assignment_item(row, question_paper=material, material=await _assignment_material(db, row[0]), exam_id=exam_ids.get(row[0].assignment_id)))
        return CourseworkAssignmentListResponse(data=data)
    if role != "STUDENT":
        raise ForbiddenError("This LMS role cannot access coursework assignments")
    student_rows = [row for row in await repo.list_for_student(user_id) if belongs_to_workspace(row[0])]
    exam_ids = await _graded_exam_ids(db, [row[0].assignment_id for row in student_rows])
    data = []
    for assignment, course, target_class, submission in student_rows:
        now = utc_now()
        if assignment.submission_type == "mcq":
            if not assignment_is_available(assignment.available_from, now) and not (submission and submission.status == "in_progress"):
                continue
            if submission and submission.status == "in_progress" and remaining_seconds(submission.expires_at) == 0:
                await _finalize_graded_attempt(db, assignment, submission.student_user_id, expired=True)
                await db.refresh(submission)
            asset = await db.get(MediaAsset, submission.attachment_asset_id) if submission and submission.attachment_asset_id else None
            material = await db.get(MediaAsset, assignment.question_paper_asset_id) if assignment.question_paper_asset_id else None
            data.append(_assignment_item((assignment, course, target_class), submission, asset, material, expose_grade=False, material=await _assignment_material(db, assignment), exam_id=exam_ids.get(assignment.assignment_id)))
            continue
        if not assignment_is_available(assignment.available_from, now) and not (submission and submission.status == "in_progress"):
            continue
        timer_finished = submission and submission.expires_at and remaining_seconds(submission.expires_at) == 0
        deadline_finished = bool(
            assignment.due_at and as_utc(assignment.due_at) <= now and not assignment.allow_late
        )
        if submission is None and deadline_finished:
            submission = LmsCourseworkSubmission(
                assignment_id=assignment.assignment_id,
                student_user_id=user_id,
                status="expired",
                started_at=as_utc(assignment.due_at) or now,
            )
            _expire_with_zero(submission, assignment.due_at)
            db.add(submission)
            await db.flush()
        elif submission and submission.status == "in_progress" and (timer_finished or (assignment.due_at and as_utc(assignment.due_at) <= now and as_utc(submission.started_at) < as_utc(assignment.due_at))):
            cutoff = min((time for time in (as_utc(submission.expires_at), as_utc(assignment.due_at)) if time is not None), default=now)
            _finalize_saved_draft(submission, cutoff)
        elif submission and submission.status == "withdrawn" and deadline_finished:
            _expire_with_zero(submission, assignment.due_at)
        elif submission and submission.status == "expired" and submission.marks_awarded is None:
            _expire_with_zero(submission, submission.expires_at or assignment.due_at)
        asset = await db.get(MediaAsset, submission.attachment_asset_id) if submission and submission.attachment_asset_id else None
        material = await db.get(MediaAsset, assignment.question_paper_asset_id) if assignment.question_paper_asset_id else None
        data.append(_assignment_item((assignment, course, target_class), submission, asset, material, expose_grade=False, material=await _assignment_material(db, assignment), exam_id=exam_ids.get(assignment.assignment_id)))
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
    if assignment.submission_type == "mcq":
        raise ValidationError("Open the multiple-choice questions from the assignment card")
    repo = CourseworkRepository(db)
    submission = await repo.get_submission(assignment_id, user_id, for_update=True)
    if submission is None:
        now = utc_now()
        if assignment.available_from and as_utc(assignment.available_from) > now:
            raise ValidationError("This assignment is not available yet")
        if assignment.due_at and as_utc(assignment.due_at) <= now and not assignment.allow_late:
            submission = LmsCourseworkSubmission(
                assignment_id=assignment_id,
                student_user_id=user_id,
                status="expired",
                started_at=as_utc(assignment.due_at),
            )
            _expire_with_zero(submission, assignment.due_at)
            db.add(submission)
            await db.commit()
            await db.refresh(submission)
            material = await db.get(MediaAsset, assignment.question_paper_asset_id) if assignment.question_paper_asset_id else None
            return _assignment_item(context, submission, question_paper=material, expose_grade=False, material=await _assignment_material(db, assignment))
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
    elif submission.status == "in_progress" and (
        remaining_seconds(submission.expires_at) == 0
        or (assignment.due_at and as_utc(assignment.due_at) <= utc_now()
            and as_utc(submission.started_at) < as_utc(assignment.due_at))
    ):
        _finalize_saved_draft(submission, submission.expires_at or assignment.due_at)
        await db.commit()
    elif submission.status in {"submitted", "reviewed", "withdrawn"}:
        if not _can_change_submission(assignment, submission, utc_now()):
            raise ValidationError("This submission can no longer be edited")
        submission.status = "in_progress"
        submission.submitted_at = None
        submission.marks_awarded = None
        submission.grade_band = None
        submission.feedback = None
        submission.marked_by = None
        submission.marked_at = None
        await db.commit()
    asset = await db.get(MediaAsset, submission.attachment_asset_id) if submission.attachment_asset_id else None
    material = await db.get(MediaAsset, assignment.question_paper_asset_id) if assignment.question_paper_asset_id else None
    return _assignment_item(context, submission, asset, material, expose_grade=False, material=await _assignment_material(db, assignment))


async def _active_submission(db: AsyncSession, assignment_id: int, user_id: int):
    context = await _student_context(db, assignment_id, user_id)
    assignment = context[0]
    if assignment.submission_type == "mcq":
        raise ValidationError("Multiple-choice answers must be saved in the quiz")
    submission = await CourseworkRepository(db).get_submission(assignment_id, user_id, for_update=True)
    if submission is None:
        raise ValidationError("Start the assignment before saving or submitting")
    if submission.status != "in_progress":
        raise ValidationError("This attempt is already finished")
    if remaining_seconds(submission.expires_at) == 0:
        _finalize_saved_draft(submission, submission.expires_at)
        await db.commit()
        raise ValidationError("The assignment timer has expired")
    if (
        assignment.due_at
        and as_utc(assignment.due_at) <= utc_now()
        and (not assignment.allow_late or as_utc(submission.started_at) < as_utc(assignment.due_at))
    ):
        _finalize_saved_draft(submission, assignment.due_at)
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


async def _validate_assignment_material(
    db: AsyncSession, asset_id: int | None, user_id: int, existing_asset_id: int | None = None,
) -> MediaAsset | None:
    if asset_id is None:
        return None
    asset = await db.get(MediaAsset, asset_id)
    if asset is None or asset.status != "ready":
        raise ValidationError("The question paper is not ready")
    if asset.folder != "assignment-materials" or (asset.created_by != user_id and asset_id != existing_asset_id):
        raise ForbiddenError("This question paper does not belong to your assignment")
    if asset.content_type != "application/pdf":
        raise ValidationError("Question papers must be PDF files")
    return asset


async def save_draft(db: AsyncSession, assignment_id: int, payload: CourseworkDraftUpdate, user_id: int):
    context, submission = await _active_submission(db, assignment_id, user_id)
    asset = await _validate_attachment(db, payload.attachment_asset_id, user_id)
    submission.answer_text = payload.answer_text
    submission.attachment_asset_id = asset.media_asset_id if asset else None
    await db.commit()
    await db.refresh(submission)
    assignment = context[0]
    material = await db.get(MediaAsset, assignment.question_paper_asset_id) if assignment.question_paper_asset_id else None
    return _assignment_item(context, submission, asset, material, expose_grade=False, material=await _assignment_material(db, assignment))


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
    assignment = context[0]
    material = await db.get(MediaAsset, assignment.question_paper_asset_id) if assignment.question_paper_asset_id else None
    return _assignment_item(context, submission, asset, material, expose_grade=False, material=await _assignment_material(db, assignment))


async def withdraw_submission(db: AsyncSession, assignment_id: int, user_id: int):
    context = await _student_context(db, assignment_id, user_id)
    assignment = context[0]
    submission = await CourseworkRepository(db).get_submission(assignment_id, user_id, for_update=True)
    if submission is None or submission.status not in {"in_progress", "submitted", "reviewed"}:
        raise ValidationError("There is no submission to delete")
    if not _can_change_submission(assignment, submission, utc_now()):
        raise ValidationError("The deadline or attempt timer has ended; this submission cannot be deleted")
    submission.status = "withdrawn"
    submission.answer_text = None
    submission.attachment_asset_id = None
    submission.submitted_at = None
    submission.marks_awarded = None
    submission.grade_band = None
    submission.feedback = None
    submission.marked_by = None
    submission.marked_at = None
    await db.commit()
    return _assignment_item(context, submission, expose_grade=False)


async def finalize_due_drafts(db: AsyncSession, now: datetime | None = None) -> int:
    """Close saved drafts even when students never reopen the assignment page."""
    now = as_utc(now) or utc_now()
    rows = (await db.execute(
        select(LmsCourseworkSubmission, LmsCourseworkAssignment)
        .join(LmsCourseworkAssignment, LmsCourseworkAssignment.assignment_id == LmsCourseworkSubmission.assignment_id)
        .where(
            LmsCourseworkAssignment.status.in_(("published", "closed")),
            LmsCourseworkAssignment.submission_type != "mcq",
            LmsCourseworkSubmission.status.in_(("in_progress", "withdrawn")),
            or_(LmsCourseworkSubmission.expires_at <= now, LmsCourseworkAssignment.due_at <= now),
        )
        .with_for_update(of=LmsCourseworkSubmission, skip_locked=True)
    )).all()
    changed = 0
    for submission, assignment in rows:
        timer_ended = submission.expires_at is not None and as_utc(submission.expires_at) <= now
        deadline_ended = assignment.due_at is not None and as_utc(assignment.due_at) <= now and as_utc(submission.started_at) < as_utc(assignment.due_at)
        if not timer_ended and not deadline_ended:
            continue
        cutoff = min((time for time in (as_utc(submission.expires_at), as_utc(assignment.due_at)) if time is not None), default=now)
        if submission.status == "withdrawn":
            _expire_with_zero(submission, cutoff)
        else:
            _finalize_saved_draft(submission, cutoff)
        changed += 1
    if changed:
        await db.commit()
    return changed


async def _graded_exam_ids(db: AsyncSession, assignment_ids: list[int]) -> dict[int, int]:
    if not assignment_ids:
        return {}
    rows = await db.execute(select(LmsExam.assignment_id, LmsExam.exam_id).where(
        LmsExam.assignment_id.in_(assignment_ids),
        LmsExam.assessment_kind == "graded_mcq",
    ))
    return {assignment_id: exam_id for assignment_id, exam_id in rows.all()}


async def _finalize_graded_attempt(db: AsyncSession, assignment: LmsCourseworkAssignment, student_user_id: int, expired: bool = False) -> None:
    from app.modules.lms import exam_service
    exam = await db.scalar(select(LmsExam).where(LmsExam.assignment_id == assignment.assignment_id, LmsExam.assessment_kind == "graded_mcq"))
    attempt = await db.scalar(select(LmsExamAttempt).where(LmsExamAttempt.exam_id == exam.exam_id, LmsExamAttempt.student_user_id == student_user_id)) if exam else None
    if exam is None or attempt is None or attempt.status != "in_progress":
        return
    await exam_service._finalize_attempt(db, exam, assignment, attempt, expired=expired)


async def list_submissions(db: AsyncSession, assignment_id: int, user_id: int):
    assignment = await CourseworkRepository(db).get_assignment(assignment_id)
    if assignment is None:
        raise NotFoundError("Assignment not found")
    await _ensure_lecturer_course(db, assignment.course_id, user_id)
    exam = await db.scalar(select(LmsExam).where(LmsExam.assignment_id == assignment_id, LmsExam.assessment_kind == "graded_mcq"))
    attempts = {}
    if exam is not None:
        attempt_rows = (await db.execute(select(LmsExamAttempt).where(LmsExamAttempt.exam_id == exam.exam_id))).scalars().all()
        attempts = {item.student_user_id: item for item in attempt_rows}
    items = []
    for submission, user, profile, asset in await CourseworkRepository(db).list_submissions(assignment_id):
        attempt = attempts.get(submission.student_user_id)
        if attempt is not None and submission.status == "in_progress" and remaining_seconds(attempt.expires_at) == 0:
            await _finalize_graded_attempt(db, assignment, submission.student_user_id, expired=True)
            await db.refresh(submission)
            await db.refresh(attempt)
        awarded = submission.marks_awarded
        if awarded is None and attempt is not None and attempt.status != "in_progress":
            awarded = attempt.auto_marks
        items.append(CourseworkSubmissionItem(
            is_late=submission_is_late(submission, assignment.due_at),
            submission_id=submission.submission_id,
            assignment_id=submission.assignment_id,
            student_user_id=submission.student_user_id,
            student_name=user.full_name or user.email,
            student_email=user.email,
            student_number=profile.student_number or "",
            status=submission.status,
            started_at=submission.started_at,
            expires_at=submission.expires_at,
            submitted_at=submission.submitted_at,
            answer_text=submission.answer_text,
            attachment_asset_id=submission.attachment_asset_id,
            attachment_url=_asset_url(asset),
            attachment_name=asset.original_filename if asset else None,
            marks_awarded=awarded,
            grade_band=(submission.grade_band or grade_band_for_score(awarded, assignment.max_marks))
            if grading_mode(assignment) == "bands" and assignment.submission_type != "mcq" and awarded is not None else None,
            auto_marks=attempt.auto_marks if attempt is not None and attempt.status != "in_progress" else None,
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
    if submission.status not in {"submitted", "expired", "reviewed", "returned"}:
        raise ValidationError("The student has not finished this attempt")
    if assignment.submission_type == "mcq":
        raise ValidationError("Quiz marks are calculated automatically from the answers")
    if grading_mode(assignment) == "marks" and payload.marks_awarded is None:
        raise ValidationError("Enter numeric marks for this CPD assignment")
    if grading_mode(assignment) == "marks":
        awarded = Decimal(payload.marks_awarded)
        if awarded > Decimal(assignment.max_marks):
            raise ValidationError(f"Marks cannot exceed {assignment.max_marks}")
    elif payload.grade_band is not None:
        awarded = band_marks(assignment.max_marks, payload.grade_band)
    elif payload.marks_awarded is not None:
        awarded = Decimal(payload.marks_awarded)
        if awarded > Decimal(assignment.max_marks):
            raise ValidationError(f"Marks cannot exceed {assignment.max_marks}")
    else:
        raise ValidationError("Choose a grade")
    submission.marks_awarded = awarded
    submission.grade_band = (payload.grade_band or grade_band_for_score(awarded, assignment.max_marks)) if grading_mode(assignment) == "bands" else None
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


async def complete_material_upload(db: AsyncSession, asset_id: int, user_id: int):
    asset = await db.get(MediaAsset, asset_id)
    if asset is None:
        raise NotFoundError("Media asset not found")
    if asset.created_by != user_id or asset.folder != "assignment-materials":
        raise ForbiddenError("This question paper upload does not belong to your account")
    return await media_service.complete_upload(db, asset_id)
