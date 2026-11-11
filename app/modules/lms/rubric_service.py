"""ATHE marking rubrics: shared templates, per-assignment rubrics, and independent 1st / 2nd assessor forms.

Visibility rules
* The 1st assessor and admins can read both forms; only the 1st assessor's form is the final feedback.
* The 2nd assessor can read and write only their own form, so their marking stays independent.
* Other lecturers on the course see marking progress only.
* Students see the 1st assessor's completed form once results are released.
"""
from datetime import date

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.auth.models import User
from app.modules.lms import coursework_service as cw
from app.modules.lms import rubric_excel, rubric_pdf
from app.modules.lms.assessment_grading import is_athe, is_athe_body
from app.modules.lms.models import (
    ClassLecturer,
    LmsAssignmentRubric,
    LmsClass,
    LmsCourseworkAssignment,
    LmsCourseworkSubmission,
    LmsRubricAssessment,
    LmsRubricResubmission,
    LmsRubricTemplate,
    LmsRubricTemplateCriterion,
    StudentProfile,
)
from app.modules.lms.repository import CourseworkRepository
from app.modules.lms.rubric_grading import NOT_YET_ACHIEVED, compute_rubric_grade, display_grade, grade_band
from app.modules.lms.schemas.rubric import (
    AssignmentRubricItem,
    AssignmentRubricWrite,
    ClassRubricContext,
    RubricAssessmentItem,
    RubricAssessmentWrite,
    RubricAttemptSummary,
    RubricCriterionItem,
    RubricMarkingResponse,
    RubricOverviewResponse,
    RubricOverviewRow,
    RubricPerson,
    RubricSubmissionInfo,
    RubricTemplateItem,
    RubricTemplateListResponse,
    RubricTemplateUpdate,
    RubricTemplateWrite,
    ResubmissionReleaseResult,
    StudentResubmission,
    StudentResubmissionWrite,
    StudentRubricFeedback,
)

MARKABLE_STATUSES = {"submitted", "expired", "reviewed", "returned"}
NEEDS_MARKING_STATUSES = {"submitted", "reviewed", "returned"}


# --------------------------------------------------------------------------- helpers

async def is_global_admin(db: AsyncSession, user_id: int) -> bool:
    return bool(await db.scalar(text("""
        SELECT 1 FROM user_access_levels ual
        JOIN access_levels al ON al.access_level_id=ual.access_level_id
        WHERE ual.user_id=:user_id AND al.access_key IN ('SUPER_ADMIN','ADMIN')
        LIMIT 1
    """), {"user_id": user_id}))


def _person_name(user: User | None) -> str:
    if user is None:
        return ""
    return user.full_name or user.email


async def _people(db: AsyncSession, user_ids) -> dict[int, User]:
    ids = [item for item in set(user_ids) if item]
    if not ids:
        return {}
    rows = (await db.execute(select(User).where(User.user_id.in_(ids)))).scalars().all()
    return {item.user_id: item for item in rows}


def viewer_role(rubric: LmsAssignmentRubric, user_id: int, admin: bool) -> str:
    if rubric.first_assessor_user_id == user_id:
        return "first"
    if rubric.second_assessor_user_id == user_id:
        return "second"
    return "admin" if admin else "viewer"


def can_read_form(role: str, form_role: str) -> bool:
    if form_role == "first":
        return role in {"first", "admin"}
    return role in {"first", "second", "admin"}


def can_write_form(role: str, form_role: str) -> bool:
    return role == form_role


async def annotate_has_rubric(db: AsyncSession, assignments) -> None:
    assignments = list(assignments)
    ids = [item.assignment_id for item in assignments]
    if not ids:
        return
    attached = set((await db.execute(
        select(LmsAssignmentRubric.assignment_id).where(LmsAssignmentRubric.assignment_id.in_(ids))
    )).scalars().all())
    for item in assignments:
        item.has_rubric = item.assignment_id in attached


async def get_assignment_rubric(db: AsyncSession, assignment_id: int) -> LmsAssignmentRubric | None:
    return await db.get(LmsAssignmentRubric, assignment_id)


async def _load_assignment(db: AsyncSession, assignment_id: int, user_id: int) -> LmsCourseworkAssignment:
    assignment = await CourseworkRepository(db).get_assignment(assignment_id)
    if assignment is None:
        raise NotFoundError("Assignment not found")
    await cw._ensure_lecturer_course(db, assignment.course_id, user_id)
    return assignment


async def _load_rubric(db: AsyncSession, assignment_id: int) -> LmsAssignmentRubric:
    rubric = await get_assignment_rubric(db, assignment_id)
    if rubric is None:
        raise NotFoundError("This assignment does not have a marking rubric yet")
    return rubric


async def _rubric_item(db: AsyncSession, rubric: LmsAssignmentRubric, role: str) -> AssignmentRubricItem:
    people = await _people(db, [rubric.first_assessor_user_id, rubric.second_assessor_user_id])
    started = bool(await db.scalar(
        select(func.count(LmsRubricAssessment.assessment_id)).where(LmsRubricAssessment.assignment_id == rubric.assignment_id)
    ))

    def person(user_id):
        return RubricPerson(user_id=user_id, name=_person_name(people.get(user_id))) if user_id else None

    return AssignmentRubricItem(
        assignment_id=rubric.assignment_id,
        template_id=rubric.template_id,
        template_name=rubric.template_name,
        form_title=rubric.form_title,
        qualification=rubric.qualification,
        unit_code_title=rubric.unit_code_title,
        assignment_title=rubric.assignment_title,
        issued_at=rubric.issued_at,
        criteria=[RubricCriterionItem(**item) for item in rubric.criteria_snapshot or []],
        first_assessor=person(rubric.first_assessor_user_id),
        second_assessor=person(rubric.second_assessor_user_id),
        viewer_role=role,
        marking_started=started,
    )


# --------------------------------------------------------------------------- templates

async def _template_criteria(db: AsyncSession, template_ids) -> dict[int, list[LmsRubricTemplateCriterion]]:
    ids = list(template_ids)
    result: dict[int, list[LmsRubricTemplateCriterion]] = {item: [] for item in ids}
    if not ids:
        return result
    rows = (await db.execute(
        select(LmsRubricTemplateCriterion)
        .where(LmsRubricTemplateCriterion.template_id.in_(ids))
        .order_by(LmsRubricTemplateCriterion.template_id, LmsRubricTemplateCriterion.position)
    )).scalars().all()
    for row in rows:
        result[row.template_id].append(row)
    return result


def _criterion_item(row: LmsRubricTemplateCriterion) -> RubricCriterionItem:
    return RubricCriterionItem(
        criterion_id=str(row.criterion_id),
        lo_code=row.lo_code,
        lo_statement=row.lo_statement or "",
        ac_code=row.ac_code,
        level=row.level,
        statement=row.statement,
    )


def _template_item(template: LmsRubricTemplate, criteria, people, user_id: int, admin: bool, include_criteria: bool) -> RubricTemplateItem:
    return RubricTemplateItem(
        template_id=template.template_id,
        name=template.name,
        form_title=template.form_title,
        qualification=template.qualification,
        unit_code_title=template.unit_code_title,
        default_assignment_title=template.default_assignment_title,
        source_file_name=template.source_file_name,
        is_archived=template.is_archived,
        created_by=template.created_by,
        created_by_name=_person_name(people.get(template.created_by)) or None,
        can_edit=admin or template.created_by == user_id,
        criteria_count=len(criteria),
        criteria=[_criterion_item(row) for row in criteria] if include_criteria else [],
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


async def list_templates(db: AsyncSession, user_id: int, include_archived: bool = False) -> RubricTemplateListResponse:
    stmt = select(LmsRubricTemplate).order_by(LmsRubricTemplate.updated_at.desc(), LmsRubricTemplate.template_id.desc())
    if not include_archived:
        stmt = stmt.where(LmsRubricTemplate.is_archived.is_(False))
    templates = list((await db.execute(stmt)).scalars().all())
    criteria = await _template_criteria(db, [item.template_id for item in templates])
    people = await _people(db, [item.created_by for item in templates])
    admin = await is_global_admin(db, user_id)
    return RubricTemplateListResponse(data=[
        _template_item(item, criteria[item.template_id], people, user_id, admin, include_criteria=True)
        for item in templates
    ])


async def get_template(db: AsyncSession, template_id: int, user_id: int) -> RubricTemplateItem:
    template = await db.get(LmsRubricTemplate, template_id)
    if template is None:
        raise NotFoundError("Rubric template not found")
    criteria = await _template_criteria(db, [template_id])
    people = await _people(db, [template.created_by])
    return _template_item(template, criteria[template_id], people, user_id, await is_global_admin(db, user_id), True)


def _validate_criteria(criteria) -> None:
    codes = [item.ac_code.casefold() for item in criteria]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        raise ValidationError(f"Each assessment criterion code must be unique. Repeated: {', '.join(duplicates)}")
    if not any(item.level == "pass" for item in criteria):
        raise ValidationError("A rubric needs at least one Pass criterion.")


def _replace_criteria(db: AsyncSession, template_id: int, criteria) -> None:
    for position, item in enumerate(criteria, start=1):
        db.add(LmsRubricTemplateCriterion(
            template_id=template_id,
            lo_code=item.lo_code,
            lo_statement=item.lo_statement or "",
            ac_code=item.ac_code,
            level=item.level,
            statement=item.statement,
            position=position,
        ))


async def create_template(db: AsyncSession, payload: RubricTemplateWrite, user_id: int) -> RubricTemplateItem:
    _validate_criteria(payload.criteria)
    template = LmsRubricTemplate(
        name=payload.name,
        form_title=payload.form_title,
        qualification=payload.qualification,
        unit_code_title=payload.unit_code_title,
        default_assignment_title=payload.default_assignment_title,
        source_file_name=payload.source_file_name,
        created_by=user_id,
        is_archived=False,
    )
    db.add(template)
    await db.flush()
    _replace_criteria(db, template.template_id, payload.criteria)
    await db.commit()
    return await get_template(db, template.template_id, user_id)


async def _editable_template(db: AsyncSession, template_id: int, user_id: int) -> LmsRubricTemplate:
    template = await db.get(LmsRubricTemplate, template_id)
    if template is None:
        raise NotFoundError("Rubric template not found")
    if template.created_by != user_id and not await is_global_admin(db, user_id):
        raise ForbiddenError("Only the lecturer who created this rubric template or an admin can change it")
    return template


async def update_template(db: AsyncSession, template_id: int, payload: RubricTemplateUpdate, user_id: int) -> RubricTemplateItem:
    template = await _editable_template(db, template_id, user_id)
    for field in ("name", "form_title", "qualification", "unit_code_title", "default_assignment_title", "is_archived"):
        value = getattr(payload, field)
        if value is not None:
            setattr(template, field, value.strip() if isinstance(value, str) else value)
    if payload.criteria is not None:
        _validate_criteria(payload.criteria)
        await db.execute(delete(LmsRubricTemplateCriterion).where(LmsRubricTemplateCriterion.template_id == template_id))
        _replace_criteria(db, template_id, payload.criteria)
    await db.commit()
    return await get_template(db, template_id, user_id)


async def delete_template(db: AsyncSession, template_id: int, user_id: int) -> None:
    template = await _editable_template(db, template_id, user_id)
    await db.delete(template)
    await db.commit()


# --------------------------------------------------------------------------- assignment rubric

async def _is_class_lecturer(db: AsyncSession, class_id: int, user_id: int) -> bool:
    return await db.get(ClassLecturer, (class_id, user_id)) is not None


async def _validate_assessor(db: AsyncSession, assignment: LmsCourseworkAssignment, user_id: int, label: str, allow_admin: bool) -> None:
    if await db.get(User, user_id) is None:
        raise ValidationError(f"The {label} account was not found")
    if assignment.target_type == "class":
        if await _is_class_lecturer(db, assignment.target_id, user_id):
            return
        if allow_admin and await is_global_admin(db, user_id):
            return
        raise ValidationError(f"The {label} must be a lecturer assigned to this class")
    try:
        await cw._ensure_lecturer_course(db, assignment.course_id, user_id)
    except ForbiddenError:
        raise ValidationError(f"The {label} must be a lecturer on this course") from None


async def attach_rubric(db: AsyncSession, assignment_id: int, payload: AssignmentRubricWrite, user_id: int) -> AssignmentRubricItem:
    assignment = await _load_assignment(db, assignment_id, user_id)
    if assignment.submission_type == "mcq":
        raise ValidationError("Multiple-choice assignments are marked automatically and do not use a rubric")
    if not is_athe(assignment):
        raise ValidationError("Marking rubrics are only available for ATHE classes")
    admin = await is_global_admin(db, user_id)
    rubric = await get_assignment_rubric(db, assignment_id)
    if rubric is not None and rubric.first_assessor_user_id != user_id and rubric.created_by != user_id and not admin:
        raise ForbiddenError("Only the 1st assessor or an admin can change this assignment's rubric")

    first_id = payload.first_assessor_user_id or (rubric.first_assessor_user_id if rubric else None) or assignment.created_by or user_id
    second_id = payload.second_assessor_user_id
    if second_id is not None and second_id == first_id:
        raise ValidationError("The 2nd assessor must be a different lecturer from the 1st assessor")
    await _validate_assessor(db, assignment, first_id, "1st assessor", allow_admin=True)
    if second_id is not None:
        await _validate_assessor(db, assignment, second_id, "2nd assessor", allow_admin=False)

    template = await db.get(LmsRubricTemplate, payload.template_id)
    if template is None:
        raise NotFoundError("Rubric template not found")
    started = bool(await db.scalar(
        select(func.count(LmsRubricAssessment.assessment_id)).where(LmsRubricAssessment.assignment_id == assignment_id)
    ))
    template_changed = rubric is None or rubric.template_id != template.template_id
    if template_changed and started:
        raise ValidationError("Marking has already started, so the rubric criteria can no longer be changed. You can still change the assessors.")
    if assignment.grades_released and rubric is not None and (template_changed or rubric.first_assessor_user_id != first_id):
        raise ValidationError("Results are already released. Withdraw the release before changing the rubric or 1st assessor.")

    criteria = (await _template_criteria(db, [template.template_id]))[template.template_id] if template_changed else []
    if template_changed and not criteria:
        raise ValidationError("This rubric template has no assessment criteria")
    is_new = rubric is None
    if is_new:
        rubric = LmsAssignmentRubric(assignment_id=assignment_id, created_by=user_id, criteria_snapshot=[], template_name=template.name)
    if template_changed:
        rubric.template_id = template.template_id
        rubric.template_name = template.name
        rubric.form_title = template.form_title
        rubric.qualification = template.qualification
        rubric.unit_code_title = template.unit_code_title
        rubric.criteria_snapshot = [_criterion_item(row).model_dump() for row in criteria]
    rubric.assignment_title = (payload.assignment_title or rubric.assignment_title or template.default_assignment_title or assignment.title)[:255]
    rubric.issued_at = payload.issued_at or rubric.issued_at or (assignment.available_from.date() if assignment.available_from else date.today())
    rubric.first_assessor_user_id = first_id
    rubric.second_assessor_user_id = second_id
    if is_new:
        db.add(rubric)
    # Forms written by a replaced assessor follow the role, but record the new owner.
    for form in (await db.execute(select(LmsRubricAssessment).where(LmsRubricAssessment.assignment_id == assignment_id))).scalars().all():
        owner = first_id if form.assessor_role == "first" else second_id
        if owner is None:
            await db.delete(form)
        else:
            form.assessor_user_id = owner
    await db.commit()
    return await _rubric_item(db, rubric, viewer_role(rubric, user_id, admin))


async def remove_rubric(db: AsyncSession, assignment_id: int, user_id: int) -> None:
    await _load_assignment(db, assignment_id, user_id)
    rubric = await _load_rubric(db, assignment_id)
    admin = await is_global_admin(db, user_id)
    if rubric.first_assessor_user_id != user_id and rubric.created_by != user_id and not admin:
        raise ForbiddenError("Only the 1st assessor or an admin can remove this rubric")
    if await db.scalar(select(func.count(LmsRubricAssessment.assessment_id)).where(LmsRubricAssessment.assignment_id == assignment_id)):
        raise ValidationError("Marking has already started with this rubric, so it cannot be removed")
    await db.delete(rubric)
    await db.commit()


async def get_rubric(db: AsyncSession, assignment_id: int, user_id: int) -> AssignmentRubricItem | None:
    await _load_assignment(db, assignment_id, user_id)
    rubric = await get_assignment_rubric(db, assignment_id)
    if rubric is None:
        return None
    return await _rubric_item(db, rubric, viewer_role(rubric, user_id, await is_global_admin(db, user_id)))


# --------------------------------------------------------------------------- assessor forms

def _form_item(form: LmsRubricAssessment | None, submission_id: int, role: str, people, attempt_no: int = 1) -> RubricAssessmentItem:
    if form is None:
        return RubricAssessmentItem(submission_id=submission_id, assessor_role=role, status="not_started", attempt_no=attempt_no)
    return RubricAssessmentItem(
        assessment_id=form.assessment_id,
        submission_id=form.submission_id,
        assessor_role=form.assessor_role,
        assessor_user_id=form.assessor_user_id,
        assessor_name=_person_name(people.get(form.assessor_user_id)) or None,
        criteria_results=form.criteria_results or [],
        actions_needed=form.actions_needed,
        overall_comment=form.overall_comment,
        resubmission=form.resubmission,
        assessment_date=form.assessment_date,
        overall_grade=display_grade(form.overall_grade),
        grade_summary=form.grade_summary,
        attempt_no=form.attempt_no or 1,
        resubmission_due_at=form.resubmission_due_at,
        status=form.status,
        completed_at=form.completed_at,
        updated_at=form.updated_at,
    )


async def _forms(db: AsyncSession, submission_id: int, attempt_no: int = 1) -> dict[str, LmsRubricAssessment]:
    rows = (await db.execute(select(LmsRubricAssessment).where(
        LmsRubricAssessment.submission_id == submission_id,
        LmsRubricAssessment.attempt_no == attempt_no,
    ))).scalars().all()
    return {item.assessor_role: item for item in rows}


# --------------------------------------------------------------------------- resubmission attempts
# Attempt 1 is the original submission. Each Resubmission grade with a deadline opens the next attempt,
# which is handed in on lms_rubric_resubmissions and marked on its own forms (attempt_no), so the
# feedback a student already received is never overwritten.

async def _resubmissions(db: AsyncSession, submission_id: int) -> list[LmsRubricResubmission]:
    return list((await db.execute(
        select(LmsRubricResubmission)
        .where(LmsRubricResubmission.submission_id == submission_id)
        .order_by(LmsRubricResubmission.attempt_no)
    )).scalars().all())


def _marking_attempt(resubmissions) -> tuple[int, LmsRubricResubmission | None]:
    """The attempt assessors are working on: the newest handed-in resubmission, else the original."""
    handed_in = [item for item in resubmissions if item.status in {"submitted", "marked", "released"}]
    return (handed_in[-1].attempt_no, handed_in[-1]) if handed_in else (1, None)


def _released_attempt(resubmissions) -> int:
    released = [item.attempt_no for item in resubmissions if item.status == "released"]
    return max(released) if released else 1


def _resubmission_open(item: LmsRubricResubmission | None, now=None) -> bool:
    return bool(item and item.status == "open" and cw.as_utc(item.due_at) > (now or cw.utc_now()))


def _student_resubmission(item: LmsRubricResubmission, asset=None) -> StudentResubmission:
    return StudentResubmission(
        attempt_no=item.attempt_no,
        due_at=item.due_at,
        status=item.status,
        is_open=_resubmission_open(item),
        answer_text=item.answer_text,
        attachment_asset_id=item.attachment_asset_id,
        attachment_url=cw._asset_url(asset),
        attachment_name=asset.original_filename if asset else None,
        submitted_at=item.submitted_at,
    )


async def open_resubmissions(db: AsyncSession, assignment, submission_ids=None) -> int:
    """After results are released, open the next attempt for every student whose released 1st assessor
    form says Resubmission and carries a deadline. Safe to call again: existing attempts are kept."""
    stmt = select(LmsCourseworkSubmission).where(LmsCourseworkSubmission.assignment_id == assignment.assignment_id)
    if submission_ids is not None:
        stmt = stmt.where(LmsCourseworkSubmission.submission_id.in_(list(submission_ids) or [0]))
    opened = []
    for submission in (await db.execute(stmt)).scalars().all():
        resubmissions = await _resubmissions(db, submission.submission_id)
        attempt = _released_attempt(resubmissions)
        if any(item.attempt_no > attempt for item in resubmissions):
            continue
        form = (await _forms(db, submission.submission_id, attempt)).get("first")
        if form is None or form.status != "completed" or grade_band(form.overall_grade) != "fail" or form.resubmission_due_at is None:
            continue
        db.add(LmsRubricResubmission(
            assignment_id=assignment.assignment_id,
            submission_id=submission.submission_id,
            student_user_id=submission.student_user_id,
            attempt_no=attempt + 1,
            due_at=form.resubmission_due_at,
            status="open",
        ))
        opened.append((submission.student_user_id, form.resubmission_due_at))
    if opened:
        await db.commit()
        from app.modules.lms import notification_service
        for student_user_id, due_at in opened:
            await notification_service.notify_resubmission_opened(db, assignment, student_user_id, due_at)
    return len(opened)


async def _submission_context(db: AsyncSession, submission_id: int, user_id: int):
    context = await CourseworkRepository(db).get_submission_context(submission_id)
    if context is None:
        raise NotFoundError("Submission not found")
    submission, assignment = context
    await cw._ensure_lecturer_course(db, assignment.course_id, user_id)
    rubric = await _load_rubric(db, assignment.assignment_id)
    return submission, assignment, rubric


async def _submission_info(db: AsyncSession, submission: LmsCourseworkSubmission, assignment, resubmission=None) -> RubricSubmissionInfo:
    student = await db.get(User, submission.student_user_id)
    profile = await db.get(StudentProfile, submission.student_user_id)
    source = resubmission or submission
    asset = await db.get(cw.MediaAsset, source.attachment_asset_id) if source.attachment_asset_id else None
    return RubricSubmissionInfo(
        submission_id=submission.submission_id,
        student_user_id=submission.student_user_id,
        student_name=_person_name(student),
        student_number=(profile.student_number if profile else "") or "",
        status=submission.status if resubmission is None else f"resubmission_{resubmission.status}",
        submitted_at=source.submitted_at,
        is_late=cw.submission_is_late(submission, assignment.due_at) if resubmission is None
        else bool(resubmission.submitted_at and cw.as_utc(resubmission.submitted_at) > cw.as_utc(resubmission.due_at)),
        answer_text=source.answer_text,
        attachment_url=cw._asset_url(asset),
        attachment_name=asset.original_filename if asset else None,
    )


async def get_marking(db: AsyncSession, submission_id: int, user_id: int) -> RubricMarkingResponse:
    submission, assignment, rubric = await _submission_context(db, submission_id, user_id)
    role = viewer_role(rubric, user_id, await is_global_admin(db, user_id))
    if role == "viewer":
        raise ForbiddenError("Only the assigned assessors or an admin can open these rubric forms")
    resubmissions = await _resubmissions(db, submission_id)
    attempt, resubmission = _marking_attempt(resubmissions)
    forms = await _forms(db, submission_id, attempt)
    people = await _people(db, [item.assessor_user_id for item in forms.values()])
    previous = []
    if can_read_form(role, "first"):
        for number in range(1, attempt):
            form = (await _forms(db, submission_id, number)).get("first")
            earlier = next((item for item in resubmissions if item.attempt_no == number), None)
            previous.append(RubricAttemptSummary(
                attempt_no=number,
                overall_grade=display_grade(form.overall_grade) if form else None,
                status=form.status if form else "not_started",
                submitted_at=earlier.submitted_at if earlier else submission.submitted_at,
                completed_at=form.completed_at if form else None,
                resubmission_due_at=form.resubmission_due_at if form else None,
                released=number <= _released_attempt(resubmissions) and assignment.grades_released,
            ))
    return RubricMarkingResponse(
        rubric=await _rubric_item(db, rubric, role),
        submission=await _submission_info(db, submission, assignment, resubmission),
        grades_released=assignment.grades_released,
        attempt_no=attempt,
        can_edit=_attempt_editable(assignment, resubmission),
        previous_attempts=previous,
        first=_form_item(forms.get("first"), submission_id, "first", people, attempt) if can_read_form(role, "first") else None,
        second=_form_item(forms.get("second"), submission_id, "second", people, attempt)
        if can_read_form(role, "second") and rubric.second_assessor_user_id else None,
    )


def _attempt_editable(assignment, resubmission: LmsRubricResubmission | None) -> bool:
    if resubmission is None:
        return not assignment.grades_released
    return resubmission.status in {"submitted", "marked"}


def _clean_results(criteria, results) -> list[dict]:
    known = {str(item["criterion_id"]) for item in criteria}
    unknown = [item.criterion_id for item in results if item.criterion_id not in known]
    if unknown:
        raise ValidationError("The form contains criteria that are not part of this rubric. Reload the page and try again.")
    by_id = {item.criterion_id: item for item in results}
    return [
        {
            "criterion_id": str(item["criterion_id"]),
            "achieved": by_id[str(item["criterion_id"])].achieved if str(item["criterion_id"]) in by_id else None,
            "evidence": (by_id[str(item["criterion_id"])].evidence if str(item["criterion_id"]) in by_id else "").strip(),
        }
        for item in criteria
    ]


def _apply_first_result(submission: LmsCourseworkSubmission, assignment, form: LmsRubricAssessment | None, user_id: int) -> None:
    """Mirror the 1st assessor's completed grade onto the submission so the gradebook and release flow use it."""
    if form is not None and form.status == "completed" and form.overall_grade:
        band = grade_band(form.overall_grade)
        submission.marks_awarded = cw.band_marks(assignment.max_marks, band)
        submission.grade_band = band
        submission.feedback = form.overall_comment
        submission.marked_by = user_id
        submission.marked_at = cw.utc_now()
        submission.status = "reviewed"
    elif submission.status == "reviewed":
        submission.marks_awarded = None
        submission.grade_band = None
        submission.feedback = None
        submission.marked_by = None
        submission.marked_at = None
        submission.status = "submitted"


async def save_assessment(
    db: AsyncSession, submission_id: int, form_role: str, payload: RubricAssessmentWrite, user_id: int,
) -> RubricMarkingResponse:
    if form_role not in {"first", "second"}:
        raise NotFoundError("Unknown assessor form")
    submission, assignment, rubric = await _submission_context(db, submission_id, user_id)
    role = viewer_role(rubric, user_id, await is_global_admin(db, user_id))
    if not can_write_form(role, form_role):
        raise ForbiddenError("Only the assigned 1st assessor can fill the 1st assessor form" if form_role == "first"
                             else "Only the assigned 2nd assessor can fill the 2nd assessor form")
    resubmissions = await _resubmissions(db, submission_id)
    attempt, resubmission = _marking_attempt(resubmissions)
    if not _attempt_editable(assignment, resubmission):
        if resubmission is not None:
            raise ValidationError("This resubmission result has already been released to the student.")
        raise ValidationError("Results have been released. Withdraw the release before changing feedback.")
    if resubmission is None and submission.status not in MARKABLE_STATUSES:
        raise ValidationError("The student has not finished this submission yet")

    criteria = rubric.criteria_snapshot or []
    results = _clean_results(criteria, payload.criteria_results)
    summary = compute_rubric_grade(criteria, results)
    if payload.complete:
        if not summary["complete"]:
            raise ValidationError("Mark every assessment criterion as Achieved or Not Achieved before completing the form")
        if form_role == "first" and not (payload.overall_comment or "").strip():
            raise ValidationError("Add the overall comment and feedback before completing the form")
    due_at = cw.as_utc(payload.resubmission_due_at) if form_role == "first" and summary["overall_grade"] == NOT_YET_ACHIEVED else None
    if payload.complete and form_role == "first" and summary["overall_grade"] == NOT_YET_ACHIEVED:
        if due_at is None:
            raise ValidationError("Set the resubmission deadline for this student before completing the form")
        if due_at <= cw.utc_now():
            raise ValidationError("The resubmission deadline must be in the future")

    forms = await _forms(db, submission_id, attempt)
    form = forms.get(form_role)
    if form is None:
        form = LmsRubricAssessment(
            assignment_id=assignment.assignment_id,
            submission_id=submission_id,
            assessor_role=form_role,
            assessor_user_id=user_id,
            attempt_no=attempt,
        )
        db.add(form)
    form.resubmission_due_at = due_at
    form.assessor_user_id = user_id
    form.criteria_results = results
    form.actions_needed = (payload.actions_needed or "").strip() or None
    form.overall_comment = (payload.overall_comment or "").strip() or None
    form.resubmission = (payload.resubmission or "").strip() or None
    form.assessment_date = payload.assessment_date or (date.today() if payload.complete else form.assessment_date)
    form.overall_grade = summary["overall_grade"]
    form.grade_summary = summary
    form.status = "completed" if payload.complete else "draft"
    form.completed_at = cw.utc_now() if payload.complete else None
    if form_role == "first" and resubmission is None:
        _apply_first_result(submission, assignment, form, user_id)
    elif form_role == "first":
        # A re-marked attempt reaches the student only when the lecturer releases re-marked results.
        resubmission.status = "marked" if form.status == "completed" else "submitted"
    await db.commit()
    return await get_marking(db, submission_id, user_id)


# --------------------------------------------------------------------------- overview and release

async def overview(db: AsyncSession, assignment_id: int, user_id: int) -> RubricOverviewResponse:
    assignment = await _load_assignment(db, assignment_id, user_id)
    rubric = await _load_rubric(db, assignment_id)
    role = viewer_role(rubric, user_id, await is_global_admin(db, user_id))
    forms = (await db.execute(select(LmsRubricAssessment).where(LmsRubricAssessment.assignment_id == assignment_id))).scalars().all()
    resubmission_rows = (await db.execute(
        select(LmsRubricResubmission).where(LmsRubricResubmission.assignment_id == assignment_id).order_by(LmsRubricResubmission.attempt_no)
    )).scalars().all()
    resubmissions_by_submission: dict[int, list[LmsRubricResubmission]] = {}
    for item in resubmission_rows:
        resubmissions_by_submission.setdefault(item.submission_id, []).append(item)
    # Attempt-1 forms decide the first release; the current attempt's forms are what each row shows.
    by_submission: dict[int, dict[str, LmsRubricAssessment]] = {}
    current_forms: dict[int, dict[str, LmsRubricAssessment]] = {}
    current_attempt = {key: _marking_attempt(value)[0] for key, value in resubmissions_by_submission.items()}
    for form in forms:
        if (form.attempt_no or 1) == 1:
            by_submission.setdefault(form.submission_id, {})[form.assessor_role] = form
        if (form.attempt_no or 1) == current_attempt.get(form.submission_id, 1):
            current_forms.setdefault(form.submission_id, {})[form.assessor_role] = form
    show_first = can_read_form(role, "first")
    show_second = can_read_form(role, "second") and rubric.second_assessor_user_id is not None
    rows = []
    for submission, user, profile, _asset in await CourseworkRepository(db).list_submissions(assignment_id):
        needs = submission.status in NEEDS_MARKING_STATUSES
        own = current_forms.get(submission.submission_id, {})
        first, second = own.get("first"), own.get("second")
        latest = (resubmissions_by_submission.get(submission.submission_id) or [None])[-1]
        resubmission_status = None
        if latest is not None:
            resubmission_status = "expired" if latest.status == "open" and not _resubmission_open(latest) else latest.status
        rows.append(RubricOverviewRow(
            submission_id=submission.submission_id,
            student_user_id=submission.student_user_id,
            student_name=user.full_name or user.email,
            student_number=profile.student_number or "",
            submission_status=submission.status,
            submitted_at=submission.submitted_at,
            is_late=cw.submission_is_late(submission, assignment.due_at),
            needs_marking=needs,
            first_status=(first.status if first else "not_started") if show_first or role == "viewer" else None,
            first_grade=display_grade(first.overall_grade) if first and show_first else None,
            second_status=(second.status if second else "not_started") if (show_second or (role == "viewer" and rubric.second_assessor_user_id)) else None,
            second_grade=display_grade(second.overall_grade) if second and show_second else None,
            attempt_no=current_attempt.get(submission.submission_id, 1),
            resubmission_status=resubmission_status,
            resubmission_due_at=latest.due_at if latest is not None else None,
        ))
    to_mark = [row for row in rows if row.needs_marking]

    def completed(row, form_role):
        form = by_submission.get(row.submission_id, {}).get(form_role)
        return form is not None and form.status == "completed"

    first_done = sum(1 for row in to_mark if completed(row, "first"))
    second_done = sum(1 for row in to_mark if completed(row, "second"))
    has_second = rubric.second_assessor_user_id is not None
    return RubricOverviewResponse(
        assignment_id=assignment_id,
        assignment_title=assignment.title,
        grades_released=assignment.grades_released,
        rubric=await _rubric_item(db, rubric, role),
        rows=rows,
        to_mark=len(to_mark),
        first_completed=first_done,
        second_completed=second_done if has_second else None,
        missing_first=len(to_mark) - first_done,
        missing_second=(len(to_mark) - second_done) if has_second else None,
        resubmissions_open=sum(1 for row in rows if row.resubmission_status == "open"),
        resubmissions_to_mark=sum(1 for row in rows if row.resubmission_status == "submitted"),
        resubmissions_ready=sum(1 for row in rows if row.resubmission_status == "marked"),
    )


async def release_resubmissions(db: AsyncSession, assignment_id: int, user_id: int) -> ResubmissionReleaseResult:
    """Send re-marked resubmission results to the students and open a further attempt where needed."""
    assignment = await _load_assignment(db, assignment_id, user_id)
    rubric = await _load_rubric(db, assignment_id)
    if not await can_manage_release(db, rubric, user_id):
        raise ForbiddenError("Only the 1st assessor or an admin can release rubric results")
    if not assignment.grades_released:
        raise ValidationError("Release the original results before releasing re-marked resubmissions")
    ready = (await db.execute(select(LmsRubricResubmission).where(
        LmsRubricResubmission.assignment_id == assignment_id, LmsRubricResubmission.status == "marked",
    ))).scalars().all()
    if not ready:
        raise ValidationError("There are no re-marked resubmissions to release")
    released = []
    for item in ready:
        submission = await db.get(LmsCourseworkSubmission, item.submission_id)
        form = (await _forms(db, item.submission_id, item.attempt_no)).get("first")
        if submission is None or form is None or form.status != "completed":
            continue
        _apply_first_result(submission, assignment, form, form.assessor_user_id)
        item.status = "released"
        item.released_at = cw.utc_now()
        released.append(item)
    await db.commit()
    from app.modules.lms import notification_service
    for item in released:
        await notification_service.notify_resubmission_result(db, assignment, item.student_user_id, item.attempt_no)
    reopened = await open_resubmissions(db, assignment, [item.submission_id for item in released])
    return ResubmissionReleaseResult(released=len(released), reopened=reopened)


async def can_manage_release(db: AsyncSession, rubric: LmsAssignmentRubric, user_id: int) -> bool:
    return rubric.first_assessor_user_id == user_id or await is_global_admin(db, user_id)


async def ensure_release_allowed(db: AsyncSession, assignment, user_id: int) -> bool:
    """Called before releasing results. Returns True when the assignment uses a rubric."""
    rubric = await get_assignment_rubric(db, assignment.assignment_id)
    if rubric is None:
        return False
    if not await can_manage_release(db, rubric, user_id):
        raise ForbiddenError("Only the 1st assessor or an admin can release rubric results")
    submissions = (await db.execute(
        select(LmsCourseworkSubmission).where(
            LmsCourseworkSubmission.assignment_id == assignment.assignment_id,
            LmsCourseworkSubmission.status.in_(tuple(NEEDS_MARKING_STATUSES)),
        )
    )).scalars().all()
    if not submissions:
        raise ValidationError("There are no submissions to release yet")
    completed = set((await db.execute(
        select(LmsRubricAssessment.submission_id).where(
            LmsRubricAssessment.assignment_id == assignment.assignment_id,
            LmsRubricAssessment.assessor_role == "first",
            LmsRubricAssessment.attempt_no == 1,
            LmsRubricAssessment.status == "completed",
        )
    )).scalars().all())
    missing = [item for item in submissions if item.submission_id not in completed]
    if missing:
        raise ValidationError(
            f"{len(missing)} submission(s) still need a completed 1st assessor form before results can be released"
        )
    return True


# --------------------------------------------------------------------------- downloads

def _form_dict(rubric: LmsAssignmentRubric, form: LmsRubricAssessment | None, learner: User | None, profile, submission, people, resubmission=None) -> dict:
    results = {str(item.get("criterion_id")): item for item in (form.criteria_results if form else []) or []}
    criteria = []
    for item in rubric.criteria_snapshot or []:
        result = results.get(str(item["criterion_id"]), {})
        criteria.append({**item, "achieved": result.get("achieved"), "evidence": result.get("evidence") or ""})
    qualification = rubric.qualification or ""
    return {
        "title": qualification.upper() if qualification else rubric.template_name,
        "subtitle": rubric.form_title or f"{rubric.unit_code_title or rubric.template_name} – Assignment Feedback and Grading Rubric",
        "learner_name": _person_name(learner),
        "learner_id": (profile.student_number if profile else "") or "",
        "qualification": qualification,
        "unit_code_title": rubric.unit_code_title or "",
        "assignment_title": rubric.assignment_title or "",
        "issued": rubric.issued_at,
        "submitted": (resubmission or submission).submitted_at,
        "resubmission": (form.resubmission if form and form.resubmission else None)
        or (f"Yes - attempt {resubmission.attempt_no}" if resubmission is not None else None),
        "attempt_no": resubmission.attempt_no if resubmission is not None else 1,
        "resubmission_due_at": form.resubmission_due_at if form and grade_band(form.overall_grade) == "fail" else None,
        "first_assessor": _person_name(people.get(rubric.first_assessor_user_id)),
        "second_assessor": _person_name(people.get(rubric.second_assessor_user_id)),
        "assessment_date": form.assessment_date if form else None,
        "overall_grade": display_grade(form.overall_grade) if form else None,
        "criteria": criteria,
        "actions_needed": form.actions_needed if form else "",
        "overall_comment": form.overall_comment if form else "",
    }


def _rubric_title(rubric) -> str:
    return rubric.template_name or rubric.form_title or rubric.assignment_title or "Feedback"


async def _feedback_file_for(db: AsyncSession, rubric, submission, form_role: str, attempt: int | None = None) -> tuple[str, bytes]:
    resubmissions = await _resubmissions(db, submission.submission_id)
    if attempt is None:
        attempt = _marking_attempt(resubmissions)[0]
    resubmission = next((item for item in resubmissions if item.attempt_no == attempt), None)
    forms = await _forms(db, submission.submission_id, attempt)
    form = forms.get(form_role)
    learner = await db.get(User, submission.student_user_id)
    profile = await db.get(StudentProfile, submission.student_user_id)
    people = await _people(db, [rubric.first_assessor_user_id, rubric.second_assessor_user_id])
    content = rubric_pdf.build_feedback_pdf(_form_dict(rubric, form, learner, profile, submission, people, resubmission))
    suffix = " - ".join(part for part in (
        f"Attempt {attempt}" if attempt > 1 else None, None if form_role == "first" else "2nd Assessor",
    ) if part) or None
    return rubric_excel.feedback_file_name(_rubric_title(rubric), _person_name(learner), suffix, ".pdf"), content


async def export_assessment(db: AsyncSession, submission_id: int, form_role: str, user_id: int) -> tuple[str, bytes]:
    if form_role not in {"first", "second"}:
        raise NotFoundError("Unknown assessor form")
    submission, _assignment, rubric = await _submission_context(db, submission_id, user_id)
    role = viewer_role(rubric, user_id, await is_global_admin(db, user_id))
    if not can_read_form(role, form_role):
        raise ForbiddenError("You cannot download this assessor form")
    return await _feedback_file_for(db, rubric, submission, form_role)


async def export_all(db: AsyncSession, assignment_id: int, user_id: int) -> tuple[str, bytes]:
    assignment = await _load_assignment(db, assignment_id, user_id)
    rubric = await _load_rubric(db, assignment_id)
    role = viewer_role(rubric, user_id, await is_global_admin(db, user_id))
    form_role = "first" if can_read_form(role, "first") else "second" if role == "second" else None
    if form_role is None:
        raise ForbiddenError("Only the assigned assessors or an admin can download these forms")
    completed = set((await db.execute(
        select(LmsRubricAssessment.submission_id).where(
            LmsRubricAssessment.assignment_id == assignment_id,
            LmsRubricAssessment.assessor_role == form_role,
            LmsRubricAssessment.attempt_no == 1,
            LmsRubricAssessment.status == "completed",
        )
    )).scalars().all())
    files = []
    for submission, _user, _profile, _asset in await CourseworkRepository(db).list_submissions(assignment_id):
        if submission.submission_id in completed:
            files.append(await _feedback_file_for(db, rubric, submission, form_role, 1))
            for item in await _resubmissions(db, submission.submission_id):
                if (await _forms(db, submission.submission_id, item.attempt_no)).get(form_role) is not None:
                    files.append(await _feedback_file_for(db, rubric, submission, form_role, item.attempt_no))
    if not files:
        raise ValidationError("No completed feedback forms to download yet")
    safe_title = rubric_excel.feedback_file_name(_rubric_title(rubric), assignment.title, "Feedback Forms", ".zip")
    return safe_title, rubric_excel.build_feedback_zip(files)


# --------------------------------------------------------------------------- student

async def _student_context(db: AsyncSession, assignment_id: int, user_id: int):
    assignment = await CourseworkRepository(db).get_assignment(assignment_id)
    if assignment is None:
        raise NotFoundError("Assignment not found")
    await cw._ensure_student_target(db, assignment, user_id)
    if not assignment.grades_released:
        raise ForbiddenError("Your feedback has not been released yet")
    rubric = await _load_rubric(db, assignment_id)
    submission = await CourseworkRepository(db).get_submission(assignment_id, user_id)
    if submission is None:
        raise NotFoundError("You do not have a submission for this assignment")
    resubmissions = await _resubmissions(db, submission.submission_id)
    attempt = _released_attempt(resubmissions)
    form = (await _forms(db, submission.submission_id, attempt)).get("first")
    if form is None or form.status != "completed":
        raise NotFoundError("Feedback is not available for this submission")
    return assignment, rubric, submission, form


async def _next_resubmission(db: AsyncSession, submission_id: int) -> LmsRubricResubmission | None:
    resubmissions = await _resubmissions(db, submission_id)
    released = _released_attempt(resubmissions)
    later = [item for item in resubmissions if item.attempt_no > released]
    return later[-1] if later else None


async def student_feedback(db: AsyncSession, assignment_id: int, user_id: int) -> StudentRubricFeedback:
    assignment, rubric, submission, form = await _student_context(db, assignment_id, user_id)
    learner = await db.get(User, user_id)
    profile = await db.get(StudentProfile, user_id)
    people = await _people(db, [form.assessor_user_id])
    return StudentRubricFeedback(
        assignment_id=assignment_id,
        assignment_title=assignment.title,
        rubric=await _rubric_item(db, rubric, "viewer"),
        learner_name=_person_name(learner),
        learner_id=(profile.student_number if profile else "") or "",
        submitted_at=submission.submitted_at,
        feedback=_form_item(form, submission.submission_id, "first", people, form.attempt_no or 1),
        attempt_no=form.attempt_no or 1,
        resubmission=await _student_resubmission_item(db, await _next_resubmission(db, submission.submission_id)),
    )


async def _student_resubmission_item(db: AsyncSession, item: LmsRubricResubmission | None) -> StudentResubmission | None:
    if item is None:
        return None
    asset = await db.get(cw.MediaAsset, item.attachment_asset_id) if item.attachment_asset_id else None
    return _student_resubmission(item, asset)


async def student_feedback_export(db: AsyncSession, assignment_id: int, user_id: int) -> tuple[str, bytes]:
    _assignment, rubric, submission, form = await _student_context(db, assignment_id, user_id)
    return await _feedback_file_for(db, rubric, submission, "first", form.attempt_no or 1)


async def _open_student_resubmission(db: AsyncSession, assignment_id: int, user_id: int) -> LmsRubricResubmission:
    _assignment, _rubric, submission, _form = await _student_context(db, assignment_id, user_id)
    item = await _next_resubmission(db, submission.submission_id)
    if item is None:
        raise NotFoundError("There is no resubmission open for this assignment")
    if item.status != "open":
        raise ValidationError("You have already handed in this resubmission")
    if not _resubmission_open(item):
        raise ValidationError("The resubmission deadline has passed")
    return item


async def save_student_resubmission(db: AsyncSession, assignment_id: int, payload: StudentResubmissionWrite, user_id: int, submit: bool) -> StudentResubmission:
    item = await _open_student_resubmission(db, assignment_id, user_id)
    asset = await cw._validate_attachment(db, payload.attachment_asset_id, user_id)
    answer = (payload.answer_text or "").strip()
    if submit and not answer and asset is None:
        raise ValidationError("Add an answer or attachment before handing in your resubmission")
    item.answer_text = answer or None
    item.attachment_asset_id = asset.media_asset_id if asset else None
    if submit:
        item.status = "submitted"
        item.submitted_at = cw.utc_now()
    await db.commit()
    await db.refresh(item)
    return _student_resubmission(item, asset)


async def annotate_student_resubmissions(db: AsyncSession, items, user_id: int) -> None:
    """Add the open or handed-in resubmission to each released ATHE assignment card for the student."""
    submission_ids = [item.submission_id for item in items if item.submission_id and item.has_rubric and item.grades_released]
    if not submission_ids:
        return
    rows = (await db.execute(select(LmsRubricResubmission).where(
        LmsRubricResubmission.submission_id.in_(submission_ids), LmsRubricResubmission.student_user_id == user_id,
    ).order_by(LmsRubricResubmission.attempt_no))).scalars().all()
    by_submission: dict[int, list[LmsRubricResubmission]] = {}
    for row in rows:
        by_submission.setdefault(row.submission_id, []).append(row)
    for item in items:
        current = by_submission.get(item.submission_id) or []
        released = _released_attempt(current)
        pending = [row for row in current if row.attempt_no > released]
        if pending:
            row = pending[-1]
            item.resubmission = {"attempt_no": row.attempt_no, "due_at": row.due_at.isoformat(), "status": row.status, "is_open": _resubmission_open(row)}


async def class_rubric_context(db: AsyncSession, class_id: int, user_id: int) -> ClassRubricContext:
    """Tell the assignment form whether a class is ATHE and which lecturers can be assessors."""
    target = await db.get(LmsClass, class_id)
    if target is None:
        raise NotFoundError("Class not found")
    await cw._ensure_lecturer_course(db, target.course_id, user_id)
    body = await db.scalar(text("""
        SELECT COALESCE(NULLIF(TRIM(ac.awarding_body), ''), pr.awarding_body, '')
        FROM lms_classes lc
        JOIN lms_courses c ON c.course_id = lc.course_id
        LEFT JOIN academic_courses ac ON ac.course_id = COALESCE(lc.academic_course_id, c.catalogue_course_id)
        LEFT JOIN programs pr ON pr.program_id = c.program_id
        WHERE lc.class_id = :class_id
    """), {"class_id": class_id}) or ""
    rows = (await db.execute(
        select(User).join(ClassLecturer, ClassLecturer.lecturer_user_id == User.user_id)
        .where(ClassLecturer.class_id == class_id).order_by(User.full_name, User.email)
    )).scalars().all()
    return ClassRubricContext(
        class_id=class_id,
        awarding_body=str(body).strip(),
        is_athe=is_athe_body(body),
        current_user_id=user_id,
        is_admin=await is_global_admin(db, user_id),
        lecturers=[RubricPerson(user_id=item.user_id, name=_person_name(item)) for item in rows],
    )
