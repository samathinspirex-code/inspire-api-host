from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.lms import content_service
from app.modules.lms.models import ClassStudent, CourseEnrollment, LmsClass, LmsLectureQuizAttempt, LmsLearningItem, LmsLearningProgress, LmsModule
from collections import defaultdict

from sqlalchemy import func, select
from app.modules.lms.schemas.progress import CourseProgressSummaryResponse, StudentProgressSummary
from app.modules.lms.repository import ContentRepository, ModuleRepository, ProgressRepository
from app.modules.lms.schemas import (
    LearningProgressResponse,
    LearningProgressUpdate,
    ProgressLearningItem,
    ProgressSection,
    StudentCourseProgressResponse,
)

VIDEO_COMPLETION_PERCENT = 85
MAX_HEARTBEAT_SECONDS = 60
FIRST_HEARTBEAT_ALLOWANCE_SECONDS = 60


def completion_percent(watched_seconds: int, duration_seconds: int | None) -> float:
    if not duration_seconds:
        return 0
    return round(min(100, watched_seconds * 100 / duration_seconds), 2)


def _module_is_open(rules, course_id: int, student_id: int, class_ids: set[int]) -> bool:
    return content_service._student_access(rules, course_id, student_id, class_ids)[0]


async def _class_ids_by_student(db: AsyncSession, course_id: int) -> dict[int, set[int]]:
    rows = (await db.execute(
        select(ClassStudent.student_user_id, ClassStudent.class_id)
        .join(LmsClass, LmsClass.class_id == ClassStudent.class_id)
        .where(LmsClass.course_id == course_id)
    )).all()
    grouped: dict[int, set[int]] = defaultdict(set)
    for student_id, class_id in rows:
        grouped[student_id].add(class_id)
    return grouped


def allowed_watch_delta(
    requested_delta: int,
    previous_activity_at: datetime | None,
    now: datetime,
) -> int:
    if requested_delta <= 0:
        return 0
    if previous_activity_at is None:
        return min(requested_delta, FIRST_HEARTBEAT_ALLOWANCE_SECONDS)
    if previous_activity_at.tzinfo is None:
        previous_activity_at = previous_activity_at.replace(tzinfo=timezone.utc)
    elapsed = max(0, int((now - previous_activity_at).total_seconds()))
    return min(requested_delta, elapsed + 5, MAX_HEARTBEAT_SECONDS)


def continuous_watched_seconds(
    previous_watched: int,
    requested_position: int,
    accepted_delta: int,
    duration: int,
) -> int:
    furthest_allowed = min(duration, previous_watched + accepted_delta)
    return min(
        duration,
        max(previous_watched, min(requested_position, furthest_allowed)),
    )


async def record_progress(
    db: AsyncSession,
    item_id: int,
    payload: LearningProgressUpdate,
    student_user_id: int,
) -> LearningProgressResponse:
    item = await content_service.get_accessible_student_item(db, item_id, student_user_id)

    repo = ProgressRepository(db)
    existing = await repo.get(item_id, student_user_id)
    now = datetime.now(timezone.utc)
    previous_watched = existing.watched_seconds if existing else 0
    previous_completed = existing.is_completed if existing else False
    previous_completed_at = existing.completed_at if existing else None
    previous_activity_at = existing.last_activity_at if existing else None

    duration = payload.duration_seconds or (existing.duration_seconds if existing else None)
    if duration is None and item.duration_minutes:
        duration = item.duration_minutes * 60

    if item.item_type == "video":
        if duration is None:
            duration = max(60, payload.position_seconds + 60)
        accepted_delta = allowed_watch_delta(
            payload.watched_seconds_delta, previous_activity_at, now
        )
        if payload.event in ("ended", "complete"):
            watched = max(previous_watched, duration)
            percent = 100.0
            completed = True
            position = duration
        else:
            watched = continuous_watched_seconds(
                previous_watched,
                payload.position_seconds,
                accepted_delta,
                duration,
            )
            percent = completion_percent(watched, duration)
            completed = previous_completed or percent >= VIDEO_COMPLETION_PERCENT
            if completed:
                percent = 100.0
                watched = duration
            position = min(payload.position_seconds, duration)
    else:
        watched = 0
        duration = None
        percent = 100.0 if (previous_completed or payload.event in ("complete", "ended")) else 0.0
        completed = previous_completed or payload.event in ("complete", "ended")
        position = 0

    progress = await repo.save(
        item_id,
        student_user_id,
        {
            "watched_seconds": watched,
            "duration_seconds": duration,
            "last_position_seconds": position,
            "completion_percent": percent,
            "is_completed": completed,
            "completed_at": previous_completed_at or (now if completed else None),
            "last_activity_at": now,
        },
    )
    return LearningProgressResponse.model_validate(progress)


async def get_course_progress(
    db: AsyncSession,
    course_id: int,
    student_user_id: int,
    requester_user_id: int,
    requester_role: str,
) -> StudentCourseProgressResponse:
    await content_service._ensure_course_access(db, course_id, requester_user_id, requester_role)
    if requester_role == "STUDENT" and requester_user_id != student_user_id:
        raise ForbiddenError("Students can only view their own learning progress")
    enrolment = await db.get(CourseEnrollment, (course_id, student_user_id))
    if enrolment is None or enrolment.status != "enrolled":
        raise NotFoundError("The student is not actively enrolled in this course")

    modules = [
        module for module in await ModuleRepository(db).list_by_course(course_id)
        if module.status == "active" and not content_service.is_practice_test_module(module)
    ]
    progress_by_item = await ProgressRepository(db).list_course_progress(
        course_id, student_user_id
    )
    content_repo = ContentRepository(db)
    module_ids = [module.module_id for module in modules]
    items_by_module = await content_repo.list_items_for_modules(module_ids)
    rules_by_module = await content_repo.list_access_for_modules(module_ids)
    class_ids = (await _class_ids_by_student(db, course_id)).get(student_user_id, set())
    all_items = [
        item
        for module in modules
        for item in items_by_module[module.module_id]
        if item.status == "published"
    ]
    item_ids = [item.learning_item_id for item in all_items]
    attempts_by_item: dict[int, list[LmsLectureQuizAttempt]] = {}
    if item_ids:
        attempts = (await db.execute(select(LmsLectureQuizAttempt).where(
            LmsLectureQuizAttempt.learning_item_id.in_(item_ids),
            LmsLectureQuizAttempt.student_user_id == student_user_id,
            LmsLectureQuizAttempt.submitted_at.is_not(None),
        ).order_by(LmsLectureQuizAttempt.created_at, LmsLectureQuizAttempt.attempt_id))).scalars().all()
        for attempt in attempts:
            attempts_by_item.setdefault(attempt.learning_item_id, []).append(attempt)
    sections = []
    total_items = 0
    completed_items = 0
    total_progress_percent = 0.0
    last_activity_at = None

    for module in modules:
        items = [item for item in items_by_module[module.module_id] if item.status == "published"]
        released = _module_is_open(rules_by_module.get(module.module_id, []), course_id, student_user_id, class_ids)
        response_items = []
        section_completed = 0
        section_progress_percent = 0.0
        for item in items:
            progress = progress_by_item.get(item.learning_item_id)
            progress_response = LearningProgressResponse.model_validate(progress) if progress else None
            item_progress_percent = progress.completion_percent if progress else 0.0
            section_progress_percent += item_progress_percent
            if progress and progress.is_completed:
                section_completed += 1
            if progress and (last_activity_at is None or progress.last_activity_at > last_activity_at):
                last_activity_at = progress.last_activity_at
            quiz_attempts = attempts_by_item.get(item.learning_item_id, [])
            quiz_percentages = [
                round((attempt.score or 0) * 100 / attempt.total_questions, 2)
                for attempt in quiz_attempts if attempt.total_questions
            ]
            response_items.append(
                ProgressLearningItem(
                    learning_item_id=item.learning_item_id,
                    title=item.title,
                    item_type=item.item_type,
                    position=item.position,
                    is_required=item.is_required,
                    progress=progress_response,
                    quiz_attempt_count=len(quiz_attempts),
                    quiz_first_attempt_percent=quiz_percentages[0] if quiz_percentages else None,
                    quiz_best_attempt_percent=max(quiz_percentages) if quiz_percentages else None,
                )
            )
        if released:
            total_items += len(items)
            completed_items += section_completed
            total_progress_percent += section_progress_percent
        sections.append(
            ProgressSection(
                module_id=module.module_id,
                title=module.title,
                position=module.position,
                total_items=len(items),
                completed_items=section_completed,
                completion_percent=round(section_progress_percent / len(items), 2) if items else 0,
                is_unlocked=released,
                items=response_items,
            )
        )

    return StudentCourseProgressResponse(
        course_id=course_id,
        student_user_id=student_user_id,
        total_items=total_items,
        completed_items=completed_items,
        completion_percent=round(total_progress_percent / total_items, 2) if total_items else 0,
        last_activity_at=last_activity_at,
        sections=sections,
    )


async def get_course_progress_summary(
    db: AsyncSession, course_id: int, requester_user_id: int,
) -> CourseProgressSummaryResponse:
    """Roster percentages in constant queries, without every student's full report."""
    await content_service._ensure_course_access(db, course_id, requester_user_id, "LECTURER")
    modules = [
        module for module in await ModuleRepository(db).list_by_course(course_id)
        if module.status == "active" and not content_service.is_practice_test_module(module)
    ]
    module_ids = [module.module_id for module in modules]
    content_repo = ContentRepository(db)
    items_by_module = await content_repo.list_items_for_modules(module_ids)
    rules_by_module = await content_repo.list_access_for_modules(module_ids)
    published_by_module = {
        module.module_id: [
            item.learning_item_id for item in items_by_module.get(module.module_id, [])
            if item.status == "published"
        ]
        for module in modules
    }
    published_ids = [item_id for ids in published_by_module.values() for item_id in ids]
    classes_by_student = await _class_ids_by_student(db, course_id)
    progress_by_student: dict[int, dict[int, float]] = defaultdict(dict)
    if published_ids:
        progress_rows = (await db.execute(select(
            LmsLearningProgress.student_user_id,
            LmsLearningProgress.learning_item_id,
            LmsLearningProgress.completion_percent,
        ).where(LmsLearningProgress.learning_item_id.in_(published_ids)))).all()
        for student_id, item_id, percent in progress_rows:
            progress_by_student[student_id][item_id] = float(percent or 0)
    enrolled_ids = (await db.execute(select(CourseEnrollment.student_user_id).where(
        CourseEnrollment.course_id == course_id, CourseEnrollment.status == "enrolled",
    ).order_by(CourseEnrollment.student_user_id))).scalars().all()
    data = []
    for student_id in enrolled_ids:
        class_ids = classes_by_student.get(student_id, set())
        accessible = [
            item_id
            for module in modules
            if _module_is_open(rules_by_module.get(module.module_id, []), course_id, student_id, class_ids)
            for item_id in published_by_module[module.module_id]
        ]
        gained = sum(progress_by_student[student_id].get(item_id, 0) for item_id in accessible)
        data.append(StudentProgressSummary(
            student_user_id=student_id,
            completion_percent=round(gained / len(accessible), 2) if accessible else 0,
        ))
    return CourseProgressSummaryResponse(course_id=course_id, data=data)
