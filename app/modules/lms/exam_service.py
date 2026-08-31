import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.auth.models import User
from app.modules.lms.coursework_service import as_utc, remaining_seconds, utc_now, _ensure_lecturer_course, _ensure_student_target
from app.modules.lms.gradebook_service import percentage
from app.modules.lms.models import (
    CourseEnrollment,
    ClassStudent,
    CourseLecturer,
    LmsClass,
    LmsCourse,
    LmsCourseworkAssignment,
    LmsCourseworkSubmission,
    LmsExam,
    LmsExamAnswer,
    LmsExamAttempt,
    LmsExamQuestion,
    StudentProfile,
)
from app.modules.lms.schemas import (
    ExamAttemptResponse,
    ExamAttemptReviewItem,
    ExamAttemptReviewListResponse,
    ExamCreate,
    ExamEditorResponse,
    ExamItem,
    ExamListResponse,
    ExamQuestionEditorItem,
    ExamQuestionUpsert,
    ExamResultResponse,
)
from app.modules.lms.schemas.exam import ExamAttemptQuestion, ExamResultAnswer, ExamReviewAnswerItem
from app.modules.lms import notification_service


def fixed_exam_expiry(started_at: datetime, duration_minutes: int, due_at: datetime | None) -> datetime:
    duration_end = as_utc(started_at) + timedelta(minutes=duration_minutes)
    due = as_utc(due_at)
    return min(duration_end, due) if due else duration_end


def shuffled(values: list[int], enabled: bool) -> list[int]:
    result = list(values)
    if enabled:
        secrets.SystemRandom().shuffle(result)
    return result


async def _question_rows(db: AsyncSession, exam_id: int) -> list[LmsExamQuestion]:
    result = await db.execute(
        select(LmsExamQuestion).where(LmsExamQuestion.exam_id == exam_id).order_by(LmsExamQuestion.position, LmsExamQuestion.question_id)
    )
    return list(result.scalars().all())


async def _exam_context(db: AsyncSession, exam_id: int):
    row = (await db.execute(
        select(LmsExam, LmsCourseworkAssignment, LmsCourse, LmsClass)
        .join(LmsCourseworkAssignment, LmsCourseworkAssignment.assignment_id == LmsExam.assignment_id)
        .join(LmsCourse, LmsCourse.course_id == LmsExam.course_id)
        .outerjoin(LmsClass, and_(LmsExam.target_type == "class", LmsClass.class_id == LmsExam.target_id))
        .where(LmsExam.exam_id == exam_id)
    )).one_or_none()
    if row is None:
        raise NotFoundError("Exam not found")
    return row


def _editor_question(item: LmsExamQuestion) -> ExamQuestionEditorItem:
    return ExamQuestionEditorItem(
        question_id=item.question_id, exam_id=item.exam_id, question_type=item.question_type,
        prompt=item.prompt, marks=item.marks, position=item.position, options=item.options,
        correct_option_index=item.correct_option_index, accepted_answers=item.accepted_answers,
    )


async def _exam_item(db: AsyncSession, context, attempt: LmsExamAttempt | None = None, expose_grade: bool = True) -> ExamItem:
    exam, assignment, course, target_class = context
    count, marks = (await db.execute(
        select(func.count(LmsExamQuestion.question_id), func.coalesce(func.sum(LmsExamQuestion.marks), 0))
        .where(LmsExamQuestion.exam_id == exam.exam_id)
    )).one()
    show_grade = expose_grade or exam.grades_released
    return ExamItem(
        exam_id=exam.exam_id, assignment_id=exam.assignment_id, course_id=exam.course_id,
        course_code=course.code, course_title=course.title, target_type=exam.target_type,
        target_id=exam.target_id, target_label=target_class.name if target_class else "Entire course",
        title=exam.title, instructions=exam.instructions, available_from=exam.available_from,
        due_at=exam.due_at, duration_minutes=exam.duration_minutes,
        randomize_questions=exam.randomize_questions, randomize_options=exam.randomize_options,
        max_marks=Decimal(marks), question_count=count, grades_released=exam.grades_released,
        status=exam.status, attempt_id=attempt.attempt_id if attempt else None,
        attempt_status=attempt.status if attempt else None, started_at=attempt.started_at if attempt else None,
        expires_at=attempt.expires_at if attempt else None, submitted_at=attempt.submitted_at if attempt else None,
        remaining_seconds=remaining_seconds(attempt.expires_at) if attempt and attempt.status == "in_progress" else None,
        total_marks=attempt.total_marks if attempt and show_grade else None,
        feedback=attempt.feedback if attempt and show_grade else None,
    )


async def create_exam(db: AsyncSession, payload: ExamCreate, user_id: int) -> ExamEditorResponse:
    await _ensure_lecturer_course(db, payload.course_id, user_id)
    if payload.target_type == "class":
        target = await db.get(LmsClass, payload.target_id)
        if target is None or target.course_id != payload.course_id:
            raise ValidationError("The selected class does not belong to this course")
    assignment = LmsCourseworkAssignment(
        course_id=payload.course_id, target_type=payload.target_type, target_id=payload.target_id,
        title=payload.title, instructions=payload.instructions, assignment_type="timed",
        available_from=payload.available_from, due_at=payload.due_at,
        duration_minutes=payload.duration_minutes, max_marks=Decimal("1"), allow_late=False,
        grades_released=False, status="draft", created_by=user_id,
    )
    db.add(assignment)
    await db.flush()
    exam = LmsExam(
        assignment_id=assignment.assignment_id, course_id=payload.course_id,
        target_type=payload.target_type, target_id=payload.target_id, title=payload.title,
        instructions=payload.instructions, available_from=payload.available_from, due_at=payload.due_at,
        duration_minutes=payload.duration_minutes, randomize_questions=payload.randomize_questions,
        randomize_options=payload.randomize_options, status="draft", created_by=user_id,
    )
    db.add(exam)
    await db.commit()
    await db.refresh(exam)
    return ExamEditorResponse(exam=await _exam_item(db, await _exam_context(db, exam.exam_id)), questions=[])


async def list_exams(db: AsyncSession, user_id: int, role: str) -> ExamListResponse:
    stmt = (
        select(LmsExam, LmsCourseworkAssignment, LmsCourse, LmsClass, LmsExamAttempt)
        .join(LmsCourseworkAssignment, LmsCourseworkAssignment.assignment_id == LmsExam.assignment_id)
        .join(LmsCourse, LmsCourse.course_id == LmsExam.course_id)
        .outerjoin(LmsClass, and_(LmsExam.target_type == "class", LmsClass.class_id == LmsExam.target_id))
    )
    if role == "LECTURER":
        stmt = stmt.join(CourseLecturer, and_(CourseLecturer.course_id == LmsExam.course_id, CourseLecturer.lecturer_user_id == user_id)).outerjoin(
            LmsExamAttempt, and_(LmsExamAttempt.exam_id == LmsExam.exam_id, LmsExamAttempt.student_user_id == -1)
        )
    elif role == "STUDENT":
        class_ids = select(ClassStudent.class_id).where(ClassStudent.student_user_id == user_id)
        stmt = stmt.join(CourseEnrollment, and_(CourseEnrollment.course_id == LmsExam.course_id, CourseEnrollment.student_user_id == user_id, CourseEnrollment.status == "enrolled")).outerjoin(
            LmsExamAttempt, and_(LmsExamAttempt.exam_id == LmsExam.exam_id, LmsExamAttempt.student_user_id == user_id)
        ).where(
            LmsExam.status.in_(("published", "closed")),
            or_(LmsExam.target_type == "course", and_(LmsExam.target_type == "class", LmsExam.target_id.in_(class_ids))),
        )
    else:
        raise ForbiddenError("This LMS role cannot access exams")
    rows = list((await db.execute(stmt.order_by(LmsExam.created_at.desc()))).all())
    data = []
    for exam, assignment, course, target_class, attempt in rows:
        if attempt and attempt.status == "in_progress" and remaining_seconds(attempt.expires_at) == 0:
            await _finalize_attempt(db, exam, assignment, attempt, expired=True)
        data.append(await _exam_item(db, (exam, assignment, course, target_class), attempt, expose_grade=role == "LECTURER"))
    return ExamListResponse(data=data)


async def get_editor(db: AsyncSession, exam_id: int, user_id: int) -> ExamEditorResponse:
    context = await _exam_context(db, exam_id)
    await _ensure_lecturer_course(db, context[0].course_id, user_id)
    questions = await _question_rows(db, exam_id)
    return ExamEditorResponse(exam=await _exam_item(db, context), questions=[_editor_question(item) for item in questions])


async def _ensure_editable(db: AsyncSession, exam: LmsExam) -> None:
    attempt_count = await db.scalar(select(func.count()).select_from(LmsExamAttempt).where(LmsExamAttempt.exam_id == exam.exam_id))
    if attempt_count:
        raise ValidationError("Questions cannot be changed after a student has started this exam")


async def _sync_max_marks(db: AsyncSession, exam: LmsExam) -> None:
    total = await db.scalar(select(func.coalesce(func.sum(LmsExamQuestion.marks), 0)).where(LmsExamQuestion.exam_id == exam.exam_id))
    assignment = await db.get(LmsCourseworkAssignment, exam.assignment_id)
    assignment.max_marks = Decimal(total) if Decimal(total) > 0 else Decimal("1")


async def add_question(db: AsyncSession, exam_id: int, payload: ExamQuestionUpsert, user_id: int) -> ExamEditorResponse:
    context = await _exam_context(db, exam_id); exam = context[0]
    await _ensure_lecturer_course(db, exam.course_id, user_id); await _ensure_editable(db, exam)
    db.add(LmsExamQuestion(exam_id=exam_id, **payload.model_dump()))
    await db.flush(); await _sync_max_marks(db, exam); await db.commit()
    return await get_editor(db, exam_id, user_id)


async def update_question(db: AsyncSession, question_id: int, payload: ExamQuestionUpsert, user_id: int) -> ExamEditorResponse:
    question = await db.get(LmsExamQuestion, question_id)
    if question is None: raise NotFoundError("Exam question not found")
    context = await _exam_context(db, question.exam_id); exam = context[0]
    await _ensure_lecturer_course(db, exam.course_id, user_id); await _ensure_editable(db, exam)
    for key, value in payload.model_dump().items(): setattr(question, key, value)
    await db.flush(); await _sync_max_marks(db, exam); await db.commit()
    return await get_editor(db, exam.exam_id, user_id)


async def delete_question(db: AsyncSession, question_id: int, user_id: int) -> ExamEditorResponse:
    question = await db.get(LmsExamQuestion, question_id)
    if question is None: raise NotFoundError("Exam question not found")
    context = await _exam_context(db, question.exam_id); exam = context[0]
    await _ensure_lecturer_course(db, exam.course_id, user_id); await _ensure_editable(db, exam)
    await db.delete(question); await db.flush(); await _sync_max_marks(db, exam); await db.commit()
    return await get_editor(db, exam.exam_id, user_id)


async def update_status(db: AsyncSession, exam_id: int, status: str, user_id: int) -> ExamEditorResponse:
    context = await _exam_context(db, exam_id); exam, assignment = context[:2]
    await _ensure_lecturer_course(db, exam.course_id, user_id)
    questions = await _question_rows(db, exam_id)
    if status == "published" and not questions: raise ValidationError("Add at least one question before publishing")
    exam.status = status; assignment.status = status
    await db.commit()
    if status == "published":
        await notification_service.notify_assessment_published(db, assignment, "exam")
    return await get_editor(db, exam_id, user_id)


async def update_grade_release(db: AsyncSession, exam_id: int, released: bool, user_id: int) -> ExamEditorResponse:
    context = await _exam_context(db, exam_id); exam, assignment = context[:2]
    await _ensure_lecturer_course(db, exam.course_id, user_id)
    exam.grades_released = released; assignment.grades_released = released
    await db.commit()
    if released:
        await notification_service.notify_grade_release(db, assignment)
    return await get_editor(db, exam_id, user_id)


async def _student_exam_context(db: AsyncSession, exam_id: int, user_id: int):
    context = await _exam_context(db, exam_id); exam = context[0]
    await _ensure_student_target(db, exam, user_id)
    if exam.status != "published": raise ForbiddenError("This exam is not open")
    return context


async def _get_attempt(db: AsyncSession, exam_id: int, user_id: int, lock: bool = False):
    stmt = select(LmsExamAttempt).where(LmsExamAttempt.exam_id == exam_id, LmsExamAttempt.student_user_id == user_id)
    if lock: stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def start_exam(db: AsyncSession, exam_id: int, user_id: int) -> ExamAttemptResponse:
    context = await _student_exam_context(db, exam_id, user_id); exam, assignment = context[:2]
    attempt = await _get_attempt(db, exam_id, user_id, lock=True)
    if attempt is None:
        now = utc_now()
        if exam.available_from and as_utc(exam.available_from) > now: raise ValidationError("This exam is not available yet")
        if exam.due_at and as_utc(exam.due_at) <= now: raise ValidationError("The exam deadline has passed")
        questions = await _question_rows(db, exam_id)
        if not questions: raise ValidationError("This exam has no questions")
        order = shuffled([item.question_id for item in questions], exam.randomize_questions)
        option_orders = {str(item.question_id): shuffled(list(range(len(item.options or []))), exam.randomize_options) for item in questions if item.question_type == "mcq"}
        attempt = LmsExamAttempt(exam_id=exam_id, student_user_id=user_id, status="in_progress", started_at=now,
            expires_at=fixed_exam_expiry(now, exam.duration_minutes, exam.due_at), question_order=order, option_orders=option_orders)
        db.add(attempt); await db.flush()
        db.add(LmsCourseworkSubmission(assignment_id=assignment.assignment_id, student_user_id=user_id,
            status="in_progress", started_at=now, expires_at=attempt.expires_at, answer_text="Exam attempt"))
        await db.commit(); await db.refresh(attempt)
    if attempt.status == "in_progress" and remaining_seconds(attempt.expires_at) == 0:
        await _finalize_attempt(db, exam, assignment, attempt, expired=True)
    if attempt.status != "in_progress": raise ValidationError("This exam attempt is already finished")
    return await _attempt_response(db, exam, attempt)


async def _attempt_response(db: AsyncSession, exam: LmsExam, attempt: LmsExamAttempt) -> ExamAttemptResponse:
    questions = {item.question_id: item for item in await _question_rows(db, exam.exam_id)}
    answers = {item.question_id: item for item in (await db.execute(select(LmsExamAnswer).where(LmsExamAnswer.attempt_id == attempt.attempt_id))).scalars().all()}
    items = []
    for position, question_id in enumerate(attempt.question_order, 1):
        question = questions.get(int(question_id))
        if not question: continue
        option_order = attempt.option_orders.get(str(question.question_id), list(range(len(question.options or []))))
        answer = answers.get(question.question_id)
        displayed_selection = option_order.index(answer.selected_option_index) if answer and answer.selected_option_index in option_order else None
        items.append(ExamAttemptQuestion(question_id=question.question_id, position=position, question_type=question.question_type,
            prompt=question.prompt, marks=question.marks, options=[question.options[index] for index in option_order] if question.options else None,
            selected_option_index=displayed_selection, answer_text=answer.answer_text if answer else None))
    return ExamAttemptResponse(attempt_id=attempt.attempt_id, exam_id=exam.exam_id, title=exam.title,
        instructions=exam.instructions, status=attempt.status, started_at=attempt.started_at, expires_at=attempt.expires_at,
        submitted_at=attempt.submitted_at, remaining_seconds=remaining_seconds(attempt.expires_at) or 0, questions=items)


async def save_answers(db: AsyncSession, attempt_id: int, payload, user_id: int) -> ExamAttemptResponse:
    attempt = await db.get(LmsExamAttempt, attempt_id, with_for_update=True)
    if attempt is None or attempt.student_user_id != user_id: raise NotFoundError("Exam attempt not found")
    exam = await db.get(LmsExam, attempt.exam_id)
    if attempt.status != "in_progress": raise ValidationError("This exam attempt is already finished")
    if remaining_seconds(attempt.expires_at) == 0:
        assignment = await db.get(LmsCourseworkAssignment, exam.assignment_id); await _finalize_attempt(db, exam, assignment, attempt, expired=True)
        raise ValidationError("The exam timer has expired and your answers were submitted")
    questions = {item.question_id: item for item in await _question_rows(db, exam.exam_id)}
    for incoming in payload.answers:
        question = questions.get(incoming.question_id)
        if question is None: raise ValidationError("An answer references a question outside this exam")
        answer = (await db.execute(select(LmsExamAnswer).where(LmsExamAnswer.attempt_id == attempt_id, LmsExamAnswer.question_id == incoming.question_id))).scalar_one_or_none()
        if answer is None:
            answer = LmsExamAnswer(attempt_id=attempt_id, question_id=incoming.question_id); db.add(answer)
        if question.question_type == "mcq":
            order = attempt.option_orders.get(str(question.question_id), [])
            if incoming.selected_option_index is not None and incoming.selected_option_index >= len(order): raise ValidationError("Choose a valid option")
            answer.selected_option_index = order[incoming.selected_option_index] if incoming.selected_option_index is not None else None
            answer.answer_text = None
        else:
            answer.answer_text = (incoming.answer_text or "").strip() or None; answer.selected_option_index = None
    await db.commit(); await db.refresh(attempt)
    return await _attempt_response(db, exam, attempt)


async def _finalize_attempt(db: AsyncSession, exam: LmsExam, assignment: LmsCourseworkAssignment, attempt: LmsExamAttempt, expired: bool = False) -> None:
    if attempt.status != "in_progress": return
    questions = await _question_rows(db, exam.exam_id)
    answer_map = {item.question_id: item for item in (await db.execute(select(LmsExamAnswer).where(LmsExamAnswer.attempt_id == attempt.attempt_id))).scalars().all()}
    auto = Decimal("0"); has_written = False
    for question in questions:
        answer = answer_map.get(question.question_id)
        if answer is None:
            answer = LmsExamAnswer(attempt_id=attempt.attempt_id, question_id=question.question_id); db.add(answer)
        if question.question_type == "mcq":
            answer.is_correct = answer.selected_option_index is not None and answer.selected_option_index == question.correct_option_index
            answer.auto_marks = question.marks if answer.is_correct else Decimal("0"); auto += Decimal(answer.auto_marks)
        else:
            has_written = True; answer.auto_marks = Decimal("0")
    attempt.auto_marks = auto; attempt.submitted_at = attempt.expires_at if expired else utc_now()
    attempt.status = "expired" if expired and has_written else ("submitted" if has_written else "reviewed")
    if not has_written: attempt.manual_marks = Decimal("0"); attempt.total_marks = auto; attempt.marked_at = utc_now()
    mirror = (await db.execute(select(LmsCourseworkSubmission).where(LmsCourseworkSubmission.assignment_id == assignment.assignment_id, LmsCourseworkSubmission.student_user_id == attempt.student_user_id))).scalar_one()
    mirror.status = "submitted" if has_written else "reviewed"; mirror.submitted_at = attempt.submitted_at
    if not has_written: mirror.marks_awarded = auto; mirror.marked_at = attempt.marked_at
    await db.commit()


async def submit_exam(db: AsyncSession, attempt_id: int, payload, user_id: int) -> ExamAttemptResponse:
    await save_answers(db, attempt_id, payload, user_id)
    attempt = await db.get(LmsExamAttempt, attempt_id, with_for_update=True); exam = await db.get(LmsExam, attempt.exam_id)
    assignment = await db.get(LmsCourseworkAssignment, exam.assignment_id)
    await _finalize_attempt(db, exam, assignment, attempt)
    return await _attempt_response(db, exam, attempt)


async def list_attempts(db: AsyncSession, exam_id: int, user_id: int) -> ExamAttemptReviewListResponse:
    context = await _exam_context(db, exam_id); exam, assignment = context[:2]
    await _ensure_lecturer_course(db, exam.course_id, user_id)
    questions = {item.question_id: item for item in await _question_rows(db, exam_id)}
    rows = (await db.execute(select(LmsExamAttempt, User, StudentProfile).join(User, User.user_id == LmsExamAttempt.student_user_id).join(StudentProfile, StudentProfile.user_id == LmsExamAttempt.student_user_id).where(LmsExamAttempt.exam_id == exam_id).order_by(User.full_name))).all()
    data = []
    for attempt, user, profile in rows:
        if attempt.status == "in_progress" and remaining_seconds(attempt.expires_at) == 0: await _finalize_attempt(db, exam, assignment, attempt, expired=True)
        answer_map = {item.question_id: item for item in (await db.execute(select(LmsExamAnswer).where(LmsExamAnswer.attempt_id == attempt.attempt_id))).scalars().all()}
        answers = []
        for question_id in attempt.question_order:
            question = questions[int(question_id)]; answer = answer_map.get(question.question_id)
            answers.append(ExamReviewAnswerItem(question_id=question.question_id, question_type=question.question_type,
                prompt=question.prompt, marks=question.marks, options=question.options, correct_option_index=question.correct_option_index,
                selected_option_index=answer.selected_option_index if answer else None, answer_text=answer.answer_text if answer else None,
                is_correct=answer.is_correct if answer else None, auto_marks=answer.auto_marks if answer else Decimal("0"),
                manual_marks=answer.manual_marks if answer else None, feedback=answer.feedback if answer else None))
        data.append(ExamAttemptReviewItem(attempt_id=attempt.attempt_id, exam_id=exam_id, student_user_id=user.user_id,
            student_name=user.full_name or user.email, student_email=user.email, student_number=profile.student_number,
            status=attempt.status, started_at=attempt.started_at, expires_at=attempt.expires_at, submitted_at=attempt.submitted_at,
            auto_marks=attempt.auto_marks, manual_marks=attempt.manual_marks, total_marks=attempt.total_marks,
            max_marks=assignment.max_marks, feedback=attempt.feedback, answers=answers))
    return ExamAttemptReviewListResponse(data=data)


async def mark_attempt(db: AsyncSession, attempt_id: int, payload, user_id: int) -> ExamAttemptReviewListResponse:
    attempt = await db.get(LmsExamAttempt, attempt_id)
    if attempt is None: raise NotFoundError("Exam attempt not found")
    exam = await db.get(LmsExam, attempt.exam_id); assignment = await db.get(LmsCourseworkAssignment, exam.assignment_id)
    await _ensure_lecturer_course(db, exam.course_id, user_id)
    if attempt.status == "in_progress": raise ValidationError("The student has not finished this exam")
    questions = {item.question_id: item for item in await _question_rows(db, exam.exam_id)}
    answer_map = {item.question_id: item for item in (await db.execute(select(LmsExamAnswer).where(LmsExamAnswer.attempt_id == attempt_id))).scalars().all()}
    marks_by_question = {item.question_id: item for item in payload.answers}
    manual = Decimal("0")
    for question in questions.values():
        if question.question_type == "mcq": continue
        mark = marks_by_question.get(question.question_id)
        awarded = Decimal(mark.marks_awarded) if mark else Decimal("0")
        if awarded > Decimal(question.marks): raise ValidationError(f"Marks for '{question.prompt[:40]}' cannot exceed {question.marks}")
        answer = answer_map.get(question.question_id)
        if answer is None: answer = LmsExamAnswer(attempt_id=attempt_id, question_id=question.question_id); db.add(answer)
        answer.manual_marks = awarded; answer.feedback = mark.feedback if mark else None; manual += awarded
    attempt.manual_marks = manual; attempt.total_marks = Decimal(attempt.auto_marks) + manual; attempt.feedback = payload.feedback
    attempt.status = "reviewed"; attempt.marked_by = user_id; attempt.marked_at = utc_now()
    mirror = (await db.execute(select(LmsCourseworkSubmission).where(LmsCourseworkSubmission.assignment_id == assignment.assignment_id, LmsCourseworkSubmission.student_user_id == attempt.student_user_id))).scalar_one()
    mirror.status = "reviewed"; mirror.marks_awarded = attempt.total_marks; mirror.feedback = payload.feedback; mirror.marked_by = user_id; mirror.marked_at = attempt.marked_at
    await db.commit()
    return await list_attempts(db, exam.exam_id, user_id)


async def get_result(db: AsyncSession, exam_id: int, user_id: int) -> ExamResultResponse:
    context = await _exam_context(db, exam_id); exam, assignment, course = context[:3]
    await _ensure_student_target(db, exam, user_id)
    if not exam.grades_released: raise ForbiddenError("This exam result has not been released")
    attempt = await _get_attempt(db, exam_id, user_id)
    if attempt is None or attempt.total_marks is None: raise NotFoundError("No marked exam result is available")
    questions = {item.question_id: item for item in await _question_rows(db, exam_id)}
    answers = {item.question_id: item for item in (await db.execute(select(LmsExamAnswer).where(LmsExamAnswer.attempt_id == attempt.attempt_id))).scalars().all()}
    result_answers = []
    for question_id in attempt.question_order:
        question = questions[int(question_id)]; answer = answers.get(question.question_id)
        awarded = Decimal(answer.auto_marks or 0) + Decimal(answer.manual_marks or 0) if answer else Decimal("0")
        result_answers.append(ExamResultAnswer(question_id=question.question_id, prompt=question.prompt,
            question_type=question.question_type, marks=question.marks, marks_awarded=awarded,
            feedback=answer.feedback if answer else None, is_correct=answer.is_correct if answer else None))
    return ExamResultResponse(exam_id=exam_id, title=exam.title, course_code=course.code, max_marks=assignment.max_marks,
        total_marks=attempt.total_marks, percentage=percentage(attempt.total_marks, assignment.max_marks),
        feedback=attempt.feedback, answers=result_answers)
