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
from app.modules.lms import rubric_excel
from app.modules.lms.assessment_grading import is_athe, is_athe_body
from app.modules.lms.models import (
    ClassLecturer,
    LmsAssignmentRubric,
    LmsClass,
    LmsCourseworkAssignment,
    LmsCourseworkSubmission,
    LmsRubricAssessment,
    LmsRubricTemplate,
    LmsRubricTemplateCriterion,
    StudentProfile,
)
from app.modules.lms.repository import CourseworkRepository
from app.modules.lms.rubric_grading import compute_rubric_grade, grade_band
from app.modules.lms.schemas.rubric import (
    AssignmentRubricItem,
    AssignmentRubricWrite,
    ClassRubricContext,
    RubricAssessmentItem,
    RubricAssessmentWrite,
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

def _form_item(form: LmsRubricAssessment | None, submission_id: int, role: str, people) -> RubricAssessmentItem:
    if form is None:
        return RubricAssessmentItem(submission_id=submission_id, assessor_role=role, status="not_started")
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
        overall_grade=form.overall_grade,
        grade_summary=form.grade_summary,
        status=form.status,
        completed_at=form.completed_at,
        updated_at=form.updated_at,
    )


async def _forms(db: AsyncSession, submission_id: int) -> dict[str, LmsRubricAssessment]:
    rows = (await db.execute(select(LmsRubricAssessment).where(LmsRubricAssessment.submission_id == submission_id))).scalars().all()
    return {item.assessor_role: item for item in rows}


async def _submission_context(db: AsyncSession, submission_id: int, user_id: int):
    context = await CourseworkRepository(db).get_submission_context(submission_id)
    if context is None:
        raise NotFoundError("Submission not found")
    submission, assignment = context
    await cw._ensure_lecturer_course(db, assignment.course_id, user_id)
    rubric = await _load_rubric(db, assignment.assignment_id)
    return submission, assignment, rubric


async def _submission_info(db: AsyncSession, submission: LmsCourseworkSubmission, assignment) -> RubricSubmissionInfo:
    student = await db.get(User, submission.student_user_id)
    profile = await db.get(StudentProfile, submission.student_user_id)
    asset = await db.get(cw.MediaAsset, submission.attachment_asset_id) if submission.attachment_asset_id else None
    return RubricSubmissionInfo(
        submission_id=submission.submission_id,
        student_user_id=submission.student_user_id,
        student_name=_person_name(student),
        student_number=(profile.student_number if profile else "") or "",
        status=submission.status,
        submitted_at=submission.submitted_at,
        is_late=cw.submission_is_late(submission, assignment.due_at),
        answer_text=submission.answer_text,
        attachment_url=cw._asset_url(asset),
        attachment_name=asset.original_filename if asset else None,
    )


async def get_marking(db: AsyncSession, submission_id: int, user_id: int) -> RubricMarkingResponse:
    submission, assignment, rubric = await _submission_context(db, submission_id, user_id)
    role = viewer_role(rubric, user_id, await is_global_admin(db, user_id))
    if role == "viewer":
        raise ForbiddenError("Only the assigned assessors or an admin can open these rubric forms")
    forms = await _forms(db, submission_id)
    people = await _people(db, [item.assessor_user_id for item in forms.values()])
    return RubricMarkingResponse(
        rubric=await _rubric_item(db, rubric, role),
        submission=await _submission_info(db, submission, assignment),
        grades_released=assignment.grades_released,
        first=_form_item(forms.get("first"), submission_id, "first", people) if can_read_form(role, "first") else None,
        second=_form_item(forms.get("second"), submission_id, "second", people)
        if can_read_form(role, "second") and rubric.second_assessor_user_id else None,
    )


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
    if assignment.grades_released:
        raise ValidationError("Results have been released. Withdraw the release before changing feedback.")
    if submission.status not in MARKABLE_STATUSES:
        raise ValidationError("The student has not finished this submission yet")

    criteria = rubric.criteria_snapshot or []
    results = _clean_results(criteria, payload.criteria_results)
    summary = compute_rubric_grade(criteria, results)
    if payload.complete:
        if not summary["complete"]:
            raise ValidationError("Mark every assessment criterion as Achieved or Not Achieved before completing the form")
        if form_role == "first" and not (payload.overall_comment or "").strip():
            raise ValidationError("Add the overall comment and feedback before completing the form")

    forms = await _forms(db, submission_id)
    form = forms.get(form_role)
    if form is None:
        form = LmsRubricAssessment(
            assignment_id=assignment.assignment_id,
            submission_id=submission_id,
            assessor_role=form_role,
            assessor_user_id=user_id,
        )
        db.add(form)
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
    if form_role == "first":
        _apply_first_result(submission, assignment, form, user_id)
    await db.commit()
    return await get_marking(db, submission_id, user_id)


# --------------------------------------------------------------------------- overview and release

async def overview(db: AsyncSession, assignment_id: int, user_id: int) -> RubricOverviewResponse:
    assignment = await _load_assignment(db, assignment_id, user_id)
    rubric = await _load_rubric(db, assignment_id)
    role = viewer_role(rubric, user_id, await is_global_admin(db, user_id))
    forms = (await db.execute(select(LmsRubricAssessment).where(LmsRubricAssessment.assignment_id == assignment_id))).scalars().all()
    by_submission: dict[int, dict[str, LmsRubricAssessment]] = {}
    for form in forms:
        by_submission.setdefault(form.submission_id, {})[form.assessor_role] = form
    show_first = can_read_form(role, "first")
    show_second = can_read_form(role, "second") and rubric.second_assessor_user_id is not None
    rows = []
    for submission, user, profile, _asset in await CourseworkRepository(db).list_submissions(assignment_id):
        needs = submission.status in NEEDS_MARKING_STATUSES
        own = by_submission.get(submission.submission_id, {})
        first, second = own.get("first"), own.get("second")
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
            first_grade=first.overall_grade if first and show_first else None,
            second_status=(second.status if second else "not_started") if (show_second or (role == "viewer" and rubric.second_assessor_user_id)) else None,
            second_grade=second.overall_grade if second and show_second else None,
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
    )


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

def _form_dict(rubric: LmsAssignmentRubric, form: LmsRubricAssessment | None, learner: User | None, profile, submission, people) -> dict:
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
        "submitted": submission.submitted_at,
        "resubmission": form.resubmission if form else None,
        "first_assessor": _person_name(people.get(rubric.first_assessor_user_id)),
        "second_assessor": _person_name(people.get(rubric.second_assessor_user_id)),
        "assessment_date": form.assessment_date if form else None,
        "overall_grade": form.overall_grade if form else None,
        "criteria": criteria,
        "actions_needed": form.actions_needed if form else "",
        "overall_comment": form.overall_comment if form else "",
    }


def _rubric_title(rubric) -> str:
    return rubric.template_name or rubric.form_title or rubric.assignment_title or "Feedback"


async def _workbook_for(db: AsyncSession, rubric, submission, form_role: str) -> tuple[str, bytes]:
    forms = await _forms(db, submission.submission_id)
    form = forms.get(form_role)
    learner = await db.get(User, submission.student_user_id)
    profile = await db.get(StudentProfile, submission.student_user_id)
    people = await _people(db, [rubric.first_assessor_user_id, rubric.second_assessor_user_id])
    content = rubric_excel.build_feedback_workbook(_form_dict(rubric, form, learner, profile, submission, people))
    suffix = None if form_role == "first" else "2nd Assessor"
    return rubric_excel.feedback_file_name(_rubric_title(rubric), _person_name(learner), suffix), content


async def export_assessment(db: AsyncSession, submission_id: int, form_role: str, user_id: int) -> tuple[str, bytes]:
    if form_role not in {"first", "second"}:
        raise NotFoundError("Unknown assessor form")
    submission, _assignment, rubric = await _submission_context(db, submission_id, user_id)
    role = viewer_role(rubric, user_id, await is_global_admin(db, user_id))
    if not can_read_form(role, form_role):
        raise ForbiddenError("You cannot download this assessor form")
    return await _workbook_for(db, rubric, submission, form_role)


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
            LmsRubricAssessment.status == "completed",
        )
    )).scalars().all())
    files = []
    for submission, _user, _profile, _asset in await CourseworkRepository(db).list_submissions(assignment_id):
        if submission.submission_id in completed:
            files.append(await _workbook_for(db, rubric, submission, form_role))
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
    forms = await _forms(db, submission.submission_id)
    form = forms.get("first")
    if form is None or form.status != "completed":
        raise NotFoundError("Feedback is not available for this submission")
    return assignment, rubric, submission, form


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
        feedback=_form_item(form, submission.submission_id, "first", people),
    )


async def student_feedback_export(db: AsyncSession, assignment_id: int, user_id: int) -> tuple[str, bytes]:
    _assignment, rubric, submission, _form = await _student_context(db, assignment_id, user_id)
    return await _workbook_for(db, rubric, submission, "first")


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
