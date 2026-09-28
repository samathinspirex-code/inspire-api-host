from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.modules.lms import coursework_service, exam_service
from app.modules.lms.models import LmsAssessmentTemplate, LmsAssessmentTemplateQuestion, LmsExam, LmsExamQuestion
from app.modules.lms.schemas.coursework import CourseworkAssignmentCreate
from app.modules.lms.schemas.exam import ExamQuestionImportRequest, ExamQuestionUpsert, PracticeTestCreate
from app.modules.lms.schemas.template_bank import (
    AssessmentTemplateCreate,
    AssessmentTemplateDetail,
    AssessmentTemplateListResponse,
    AssessmentTemplateSummary,
    AssessmentTemplateUpdate,
    BankQuestionItem,
    TemplateApplyRequest,
    TemplateApplyResponse,
)


def _question_item(row: LmsAssessmentTemplateQuestion) -> BankQuestionItem:
    return BankQuestionItem(
        question_id=row.question_id,
        question_type=row.question_type,
        prompt=row.prompt,
        marks=row.marks,
        position=row.position,
        options=row.options,
        correct_option_index=row.correct_option_index,
        correct_option_indices=row.correct_option_indices,
        accepted_answers=row.accepted_answers,
    )


def _summary(template: LmsAssessmentTemplate, question_count: int) -> AssessmentTemplateSummary:
    return AssessmentTemplateSummary(
        template_id=template.template_id,
        kind=template.kind,
        template_name=template.template_name or template.title,
        title=template.title,
        instructions=template.instructions,
        assignment_type=template.assignment_type,
        submission_type=template.submission_type,
        duration_minutes=template.duration_minutes,
        max_marks=template.max_marks,
        question_count=question_count,
        updated_at=template.updated_at,
    )


async def _questions(db: AsyncSession, template_id: int) -> list[LmsAssessmentTemplateQuestion]:
    return list((await db.execute(
        select(LmsAssessmentTemplateQuestion)
        .where(LmsAssessmentTemplateQuestion.template_id == template_id)
        .order_by(LmsAssessmentTemplateQuestion.position, LmsAssessmentTemplateQuestion.question_id)
    )).scalars().all())


async def _get_template(db: AsyncSession, template_id: int) -> LmsAssessmentTemplate:
    template = await db.get(LmsAssessmentTemplate, template_id)
    if template is None:
        raise NotFoundError("Question bank not found")
    return template


async def _sync_marks(db: AsyncSession, template: LmsAssessmentTemplate) -> None:
    count = await db.scalar(select(func.count(LmsAssessmentTemplateQuestion.question_id)).where(
        LmsAssessmentTemplateQuestion.template_id == template.template_id
    ))
    if not count:
        return
    total = await db.scalar(select(func.coalesce(func.sum(LmsAssessmentTemplateQuestion.marks), 0)).where(
        LmsAssessmentTemplateQuestion.template_id == template.template_id
    ))
    template.max_marks = Decimal(total or 0)
    template.updated_at = datetime.now(timezone.utc)


def _check_question(template: LmsAssessmentTemplate, payload: ExamQuestionUpsert) -> ExamQuestionUpsert:
    if (template.kind == "practice_test" or template.submission_type == "mcq") and payload.question_type not in {"mcq", "multiple_answer"}:
        raise ValidationError("This template supports multiple-choice questions only")
    return payload


def _assignment_instructions(template: LmsAssessmentTemplate, questions: list[LmsAssessmentTemplateQuestion]) -> str:
    lines = [template.instructions.strip()]
    if questions:
        lines.extend(["", "Questions"])
        for index, question in enumerate(questions, start=1):
            lines.append(f"{index}. ({question.marks} marks) {question.prompt}")
            for option_index, option in enumerate(question.options or []):
                lines.append(f"   {chr(65 + option_index)}. {option}")
    text = "\n".join(lines).strip()
    return text[:20_000]


async def list_templates(db: AsyncSession, kind: str) -> AssessmentTemplateListResponse:
    rows = (await db.execute(
        select(LmsAssessmentTemplate, func.count(LmsAssessmentTemplateQuestion.question_id))
        .outerjoin(
            LmsAssessmentTemplateQuestion,
            LmsAssessmentTemplateQuestion.template_id == LmsAssessmentTemplate.template_id,
        )
        .where(LmsAssessmentTemplate.kind == kind)
        .group_by(LmsAssessmentTemplate.template_id)
        .order_by(LmsAssessmentTemplate.updated_at.desc())
    )).all()
    return AssessmentTemplateListResponse(data=[_summary(template, int(count or 0)) for template, count in rows])


async def create_template(
    db: AsyncSession, payload: AssessmentTemplateCreate, user_id: int,
) -> AssessmentTemplateDetail:
    template = LmsAssessmentTemplate(
        kind=payload.kind,
        template_name=payload.template_name,
        title=payload.title,
        instructions=payload.instructions,
        assignment_type=payload.assignment_type,
        submission_type=payload.submission_type,
        duration_minutes=payload.duration_minutes,
        max_marks=payload.max_marks,
        created_by=user_id,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return await get_template(db, template.template_id)


async def get_template(db: AsyncSession, template_id: int) -> AssessmentTemplateDetail:
    template = await _get_template(db, template_id)
    questions = await _questions(db, template_id)
    summary = _summary(template, len(questions))
    return AssessmentTemplateDetail(**summary.model_dump(), questions=[_question_item(item) for item in questions])


async def update_template(
    db: AsyncSession, template_id: int, payload: AssessmentTemplateUpdate,
) -> AssessmentTemplateDetail:
    template = await _get_template(db, template_id)
    template.template_name = payload.template_name
    template.title = payload.title
    template.instructions = payload.instructions
    template.max_marks = payload.max_marks
    if template.kind == "practice_test":
        template.assignment_type = "timed"
        template.submission_type = "written"
        template.duration_minutes = payload.duration_minutes or template.duration_minutes or 30
    else:
        template.assignment_type = "timed" if payload.submission_type == "mcq" else payload.assignment_type
        template.submission_type = payload.submission_type
        template.duration_minutes = payload.duration_minutes if template.assignment_type == "timed" or payload.submission_type == "mcq" else None
        if template.assignment_type == "timed" and template.duration_minutes is None:
            template.duration_minutes = 60
    template.updated_at = datetime.now(timezone.utc)
    await _sync_marks(db, template)
    await db.commit()
    return await get_template(db, template_id)


async def delete_template(db: AsyncSession, template_id: int) -> None:
    template = await _get_template(db, template_id)
    await db.delete(template)
    await db.commit()


async def add_question(
    db: AsyncSession, template_id: int, payload: ExamQuestionUpsert,
) -> AssessmentTemplateDetail:
    template = await _get_template(db, template_id)
    payload = _check_question(template, payload)
    position = int(await db.scalar(select(func.coalesce(func.max(LmsAssessmentTemplateQuestion.position), 0)).where(
        LmsAssessmentTemplateQuestion.template_id == template_id
    )) or 0) + 1
    values = payload.model_dump()
    values["position"] = position
    db.add(LmsAssessmentTemplateQuestion(template_id=template_id, **values))
    await db.flush()
    await _sync_marks(db, template)
    await db.commit()
    return await get_template(db, template_id)


async def update_question(
    db: AsyncSession, template_id: int, question_id: int, payload: ExamQuestionUpsert,
) -> AssessmentTemplateDetail:
    template = await _get_template(db, template_id)
    question = await db.get(LmsAssessmentTemplateQuestion, question_id)
    if question is None or question.template_id != template_id:
        raise NotFoundError("Question not found")
    payload = _check_question(template, payload)
    for key, value in payload.model_dump().items():
        if key != "position":
            setattr(question, key, value)
    await _sync_marks(db, template)
    await db.commit()
    return await get_template(db, template_id)


async def delete_question(db: AsyncSession, template_id: int, question_id: int) -> AssessmentTemplateDetail:
    template = await _get_template(db, template_id)
    question = await db.get(LmsAssessmentTemplateQuestion, question_id)
    if question is None or question.template_id != template_id:
        raise NotFoundError("Question not found")
    await db.delete(question)
    await db.flush()
    await _sync_marks(db, template)
    await db.commit()
    return await get_template(db, template_id)


async def import_questions(
    db: AsyncSession, template_id: int, payload: ExamQuestionImportRequest,
) -> AssessmentTemplateDetail:
    template = await _get_template(db, template_id)
    if template.kind not in {"practice_test", "assignment"} or (template.kind == "assignment" and template.submission_type != "mcq"):
        raise ValidationError("Direct MCQ import is available for practice tests and multiple-choice assignment templates")
    parsed = exam_service.parse_mcq_import(payload.raw_text, payload.marks_per_question)
    start = int(await db.scalar(select(func.coalesce(func.max(LmsAssessmentTemplateQuestion.position), 0)).where(
        LmsAssessmentTemplateQuestion.template_id == template_id
    )) or 0)
    for offset, question in enumerate(parsed, start=1):
        values = question.model_dump()
        values["position"] = start + offset
        db.add(LmsAssessmentTemplateQuestion(template_id=template_id, **values))
    await db.flush()
    await _sync_marks(db, template)
    await db.commit()
    return await get_template(db, template_id)


async def apply_template(
    db: AsyncSession, template_id: int, payload: TemplateApplyRequest, user_id: int,
) -> TemplateApplyResponse:
    template = await _get_template(db, template_id)
    questions = await _questions(db, template_id)
    if payload.class_id is None:
        target_type, target_id = "course", payload.course_id
    else:
        target_type, target_id = "class", payload.class_id
    if template.kind == "practice_test":
        if not questions:
            raise ValidationError("Add at least one question to this practice test template before using it")
        duration = payload.duration_minutes or template.duration_minutes or 30
        editor = await exam_service.create_practice_test(db, payload.course_id, PracticeTestCreate(
            target_type=target_type,
            target_id=target_id,
            title=payload.title or template.title,
            instructions=payload.instructions or template.instructions,
            available_from=payload.available_from,
            due_at=payload.due_at,
            duration_minutes=duration,
            randomize_questions=payload.randomize_questions,
            randomize_options=payload.randomize_options,
        ), user_id)
        exam_id = editor.exam.exam_id
        for question in questions:
            db.add(LmsExamQuestion(
                exam_id=exam_id,
                question_type=question.question_type,
                prompt=question.prompt,
                marks=question.marks,
                position=question.position,
                options=question.options,
                correct_option_index=question.correct_option_index,
                correct_option_indices=question.correct_option_indices,
                accepted_answers=question.accepted_answers,
            ))
        await db.flush()
        exam = await db.get(LmsExam, exam_id)
        await exam_service._sync_max_marks(db, exam)
        await db.commit()
        if payload.status == "published":
            editor = await exam_service.update_status(db, exam_id, "published", user_id)
        else:
            editor = await exam_service.get_editor(db, exam_id, user_id)
        return TemplateApplyResponse(kind="practice_test", title=template.title, exam=editor)

    duration = payload.duration_minutes or template.duration_minutes
    assignment_type = payload.assignment_type or template.assignment_type or "regular"
    submission_type = payload.submission_type or template.submission_type or "written"
    if submission_type == "mcq":
        assignment_type = "timed"
    if assignment_type == "timed" and duration is None:
        duration = 60
    created = await coursework_service.create_assignment(db, CourseworkAssignmentCreate(
        course_id=payload.course_id,
        target_type=target_type,
        target_id=target_id,
        title=payload.title or template.title,
        instructions=payload.instructions or (_assignment_instructions(template, questions) if submission_type != "mcq" else template.instructions),
        assignment_type=assignment_type,
        submission_type=submission_type,
        available_from=payload.available_from,
        due_at=payload.due_at,
        duration_minutes=duration if assignment_type == "timed" or submission_type == "mcq" else None,
        max_marks=payload.max_marks or (template.max_marks if template.max_marks and Decimal(template.max_marks) > 0 else Decimal("100")),
        allow_late=False,
        status="draft" if submission_type == "mcq" else payload.status,
    ), user_id)
    if submission_type == "mcq" and questions and created.exam_id:
        for question in questions:
            db.add(LmsExamQuestion(
                exam_id=created.exam_id,
                question_type=question.question_type,
                prompt=question.prompt,
                marks=question.marks,
                position=question.position,
                options=question.options,
                correct_option_index=question.correct_option_index,
                correct_option_indices=question.correct_option_indices,
                accepted_answers=question.accepted_answers,
            ))
        await db.flush()
        exam = await db.get(LmsExam, created.exam_id)
        await exam_service._sync_max_marks(db, exam)
        await db.commit()
    return TemplateApplyResponse(kind="assignment", title=payload.title or template.title, assignment=created)
