from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.lms import content_service
from app.modules.lms.models import CourseEnrollment
from app.modules.lms.repository import ContentRepository, ModuleRepository, ProgressRepository
from app.modules.lms.schemas import (
    LearningProgressResponse,
    LearningProgressUpdate,
    ProgressLearningItem,
    ProgressSection,
    StudentCourseProgressResponse,
)

MAX_HEARTBEAT_SECONDS = 30
FIRST_HEARTBEAT_ALLOWANCE_SECONDS = 15


def completion_percent(watched_seconds: int, duration_seconds: int | None) -> float:
    if not duration_seconds:
        return 0
    return round(min(100, watched_seconds * 100 / duration_seconds), 2)


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
    return min(requested_delta, elapsed + 2, MAX_HEARTBEAT_SECONDS)


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


def video_completion_state(
    watched_seconds: int,
    duration_seconds: int,
    event: str,
    previously_completed: bool = False,
) -> tuple[int, float, bool]:
    """Return a stable video completion state for storage and display.

    A valid Vimeo ``ended`` event is only accepted when the server has already
    observed playback to within two seconds of the end. Once completed, the
    stored watched time and percentage are normalized to the exact duration and
    100 percent so a later heartbeat cannot leave the UI at 99 percent.
    """
    percent = completion_percent(watched_seconds, duration_seconds)
    completed = previously_completed or (
        event == "ended" and watched_seconds >= max(0, duration_seconds - 2)
    )
    if completed:
        return duration_seconds, 100.0, True
    return watched_seconds, percent, False


async def record_progress(
    db: AsyncSession,
    item_id: int,
    payload: LearningProgressUpdate,
    student_user_id: int,
) -> LearningProgressResponse:
    item = await ContentRepository(db).get_item(item_id)
    if item is None:
        raise NotFoundError(f"Learning item {item_id} not found")
    module = await ModuleRepository(db).get(item.module_id)
    if module is None:
        raise NotFoundError(f"Section {item.module_id} not found")
    await content_service._ensure_course_access(db, module.course_id, student_user_id, "STUDENT")
    if module.status != "active" or item.status != "published":
        raise ForbiddenError("This learning item is not published")
    class_ids = await content_service._student_class_ids(db, module.course_id, student_user_id)
    rules = await ContentRepository(db).list_access(module.module_id)
    unlocked, _reason = content_service._student_access(
        rules, module.course_id, student_user_id, class_ids
    )
    if not unlocked:
        raise ForbiddenError("This learning item has not been released to you")

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
            raise ValidationError("Video duration is required to record progress")
        accepted_delta = allowed_watch_delta(
            payload.watched_seconds_delta, previous_activity_at, now
        )
        # Progress follows the furthest continuously watched position. Replaying an
        # earlier segment cannot inflate progress, and a forged/forward-seeked
        # position can advance only by the server-approved playback delta.
        watched = continuous_watched_seconds(
            previous_watched,
            payload.position_seconds,
            accepted_delta,
            duration,
        )
        watched, percent, completed = video_completion_state(
            watched,
            duration,
            payload.event,
            previous_completed,
        )
        position = duration if completed else min(payload.position_seconds, watched)
    else:
        watched = previous_watched
        percent = 100.0 if previous_completed or payload.event == "complete" else 0.0
        completed = previous_completed or payload.event == "complete"
        position = payload.position_seconds

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
        if module.status == "active"
    ]
    progress_by_item = await ProgressRepository(db).list_course_progress(
        course_id, student_user_id
    )
    content_repo = ContentRepository(db)
    sections = []
    total_items = 0
    completed_items = 0
    total_progress_percent = 0.0
    last_activity_at = None

    for module in modules:
        items = [item for item in await content_repo.list_items(module.module_id) if item.status == "published"]
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
            response_items.append(
                ProgressLearningItem(
                    learning_item_id=item.learning_item_id,
                    title=item.title,
                    item_type=item.item_type,
                    position=item.position,
                    is_required=item.is_required,
                    progress=progress_response,
                )
            )
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
