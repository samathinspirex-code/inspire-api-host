from datetime import datetime, timezone
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.auth.models import User
from app.modules.lms.models import ClassStudent, CourseEnrollment, CourseLecturer, LmsClass
from app.modules.lms.repository import ContentRepository, CourseRepository, ModuleRepository, ProgressRepository
from app.modules.lms.schemas import (
    CourseStudioResponse,
    CourseDiscussionCreate,
    CourseDiscussionItem,
    CourseDiscussionListResponse,
    LearningItemCreate,
    LearningItemReorderRequest,
    LearningItemResponse,
    LearningItemUpdate,
    ModuleAccessResponse,
    ModuleAccessUpdate,
    StudioSectionResponse,
)


async def _discussion_item(
    db: AsyncSession,
    discussion,
    author_name: str | None,
    author_email: str,
) -> CourseDiscussionItem:
    lecturer = await db.get(CourseLecturer, (discussion.course_id, discussion.author_user_id))
    return CourseDiscussionItem(
        discussion_id=discussion.discussion_id,
        course_id=discussion.course_id,
        author_user_id=discussion.author_user_id,
        author_name=author_name or author_email,
        author_role="LECTURER" if lecturer is not None else "STUDENT",
        message=discussion.message,
        created_at=discussion.created_at,
    )


async def _ensure_course_access(db: AsyncSession, course_id: int, user_id: int, role: str) -> None:
    if await CourseRepository(db).get(course_id) is None:
        raise NotFoundError(f"Course {course_id} not found")
    if role == "LECTURER":
        relation = await db.get(CourseLecturer, (course_id, user_id))
        if relation is None:
            raise ForbiddenError("This course is not assigned to your lecturer profile")
    elif role == "STUDENT":
        relation = await db.get(CourseEnrollment, (course_id, user_id))
        if relation is None or relation.status != "enrolled":
            raise ForbiddenError("You are not enrolled in this course")
    else:
        raise ForbiddenError("This LMS role cannot open Course Studio")


async def _ensure_module_manager(db: AsyncSession, module_id: int, user_id: int):
    module = await ModuleRepository(db).get(module_id)
    if module is None:
        raise NotFoundError(f"Section {module_id} not found")
    await _ensure_course_access(db, module.course_id, user_id, "LECTURER")
    return module


async def ensure_course_manager(db: AsyncSession, course_id: int, user_id: int) -> None:
    await _ensure_course_access(db, course_id, user_id, "LECTURER")


async def ensure_module_manager(db: AsyncSession, module_id: int, user_id: int):
    return await _ensure_module_manager(db, module_id, user_id)


async def _student_class_ids(db: AsyncSession, course_id: int, user_id: int) -> set[int]:
    stmt = (
        select(ClassStudent.class_id)
        .join(LmsClass, LmsClass.class_id == ClassStudent.class_id)
        .where(LmsClass.course_id == course_id, ClassStudent.student_user_id == user_id)
    )
    return set((await db.execute(stmt)).scalars().all())


def _student_access(rules, course_id: int, user_id: int, class_ids: set[int]) -> tuple[bool, str | None]:
    candidates = [rule for rule in rules if rule.scope_type == "student" and rule.scope_id == user_id]
    candidates += [rule for rule in rules if rule.scope_type == "class" and rule.scope_id in class_ids]
    candidates += [rule for rule in rules if rule.scope_type == "course" and rule.scope_id == course_id]
    if not candidates:
        return False, "This section has not been released yet."
    rule = candidates[0]
    if not rule.is_unlocked:
        return False, "This section is currently locked by your lecturer."
    now = datetime.now(timezone.utc)
    if rule.available_from and rule.available_from > now:
        return False, f"Available from {rule.available_from.isoformat()}"
    return True, None


def _student_stream_url(item) -> str | None:
    if item.item_type != "video" or not item.resource_url:
        return item.resource_url
    match = re.search(r"vimeo\.com/(?:video/)?(\d+)", item.resource_url)
    if not match:
        return item.resource_url
    return (
        f"https://player.vimeo.com/video/{match.group(1)}"
        "?dnt=1&download=0&pip=0&title=0&byline=0&portrait=0"
    )


def _item_response(
    item, expose_resource: bool = True, progress=None, allow_download: bool = True
) -> LearningItemResponse:
    data = LearningItemResponse.model_validate(item)
    updates = {}
    if not expose_resource:
        updates.update(resource_url=None, text_content=None)
    elif not allow_download:
        updates.update(resource_url=_student_stream_url(item), download_allowed=False)
    if progress is not None:
        updates.update(
            progress_percent=progress.completion_percent,
            is_completed=progress.is_completed,
            watched_seconds=progress.watched_seconds,
            duration_seconds=progress.duration_seconds,
            last_position_seconds=progress.last_position_seconds,
        )
    return data.model_copy(update=updates) if updates else data


async def get_course_studio(
    db: AsyncSession, course_id: int, user_id: int, role: str
) -> CourseStudioResponse:
    await _ensure_course_access(db, course_id, user_id, role)
    modules = await ModuleRepository(db).list_by_course(course_id)
    class_ids = await _student_class_ids(db, course_id, user_id) if role == "STUDENT" else set()
    sections = []
    repo = ContentRepository(db)
    progress_by_item = (
        await ProgressRepository(db).list_course_progress(course_id, user_id)
        if role == "STUDENT" else {}
    )
    for module in modules:
        if role == "STUDENT" and module.status != "active":
            continue
        rules = await repo.list_access(module.module_id)
        unlocked, reason = (True, None) if role == "LECTURER" else _student_access(rules, course_id, user_id, class_ids)
        items = await repo.list_items(module.module_id)
        if role == "STUDENT":
            items = [item for item in items if item.status == "published"]
        sections.append(
            StudioSectionResponse(
                module_id=module.module_id,
                course_id=course_id,
                title=module.title,
                description=module.description,
                position=module.position,
                status=module.status,
                is_unlocked=unlocked,
                locked_reason=reason,
                items=[
                    _item_response(
                        item,
                        unlocked or role == "LECTURER",
                        progress_by_item.get(item.learning_item_id),
                        role == "LECTURER",
                    )
                    for item in items
                ],
                access_rules=[ModuleAccessResponse.model_validate(rule) for rule in rules] if role == "LECTURER" else [],
            )
        )
    return CourseStudioResponse(course_id=course_id, can_manage=role == "LECTURER", sections=sections)


async def create_learning_item(db: AsyncSession, module_id: int, payload: LearningItemCreate, user_id: int):
    await _ensure_module_manager(db, module_id, user_id)
    repo = ContentRepository(db)
    data = payload.model_dump()
    data.update(
        module_id=module_id,
        title=payload.title.strip(),
        description=payload.description.strip() if payload.description else None,
        text_content=payload.text_content.strip() if payload.text_content else None,
        resource_url=payload.resource_url.strip() if payload.resource_url else None,
        position=await repo.next_position(module_id),
        created_by=user_id,
    )
    return LearningItemResponse.model_validate(await repo.create_item(data))


async def update_learning_item(db: AsyncSession, item_id: int, payload: LearningItemUpdate, user_id: int):
    repo = ContentRepository(db)
    item = await repo.get_item(item_id)
    if item is None:
        raise NotFoundError(f"Learning item {item_id} not found")
    await _ensure_module_manager(db, item.module_id, user_id)
    data = payload.model_dump()
    data.update(
        title=payload.title.strip(),
        description=payload.description.strip() if payload.description else None,
        text_content=payload.text_content.strip() if payload.text_content else None,
        resource_url=payload.resource_url.strip() if payload.resource_url else None,
    )
    return LearningItemResponse.model_validate(await repo.update_item(item, data))


async def delete_learning_item(db: AsyncSession, item_id: int, user_id: int) -> None:
    repo = ContentRepository(db)
    item = await repo.get_item(item_id)
    if item is None:
        raise NotFoundError(f"Learning item {item_id} not found")
    await _ensure_module_manager(db, item.module_id, user_id)
    await repo.delete_item(item)


async def reorder_learning_items(
    db: AsyncSession, module_id: int, payload: LearningItemReorderRequest, user_id: int
) -> list[LearningItemResponse]:
    await _ensure_module_manager(db, module_id, user_id)
    repo = ContentRepository(db)
    items = await repo.list_items(module_id)
    existing = {item.learning_item_id for item in items}
    requested = payload.learning_item_ids
    if len(requested) != len(set(requested)) or set(requested) != existing:
        raise ValidationError("learning_item_ids must contain every item in this section exactly once")
    return [LearningItemResponse.model_validate(item) for item in await repo.reorder_items(items, requested)]


async def update_module_access(
    db: AsyncSession, module_id: int, payload: ModuleAccessUpdate, user_id: int
) -> ModuleAccessResponse:
    module = await _ensure_module_manager(db, module_id, user_id)
    if payload.scope_type == "course" and payload.scope_id != module.course_id:
        raise ValidationError("Course access scope must use this section's course_id")
    if payload.scope_type == "class":
        class_ = await db.get(LmsClass, payload.scope_id)
        if class_ is None or class_.course_id != module.course_id:
            raise ValidationError("The selected class does not belong to this course")
    if payload.scope_type == "student":
        enrolment = await db.get(CourseEnrollment, (module.course_id, payload.scope_id))
        if enrolment is None or enrolment.status != "enrolled":
            raise ValidationError("The selected student is not enrolled in this course")
    rule = await ContentRepository(db).upsert_access(
        module_id, {**payload.model_dump(), "created_by": user_id}
    )
    return ModuleAccessResponse.model_validate(rule)


async def list_course_discussions(
    db: AsyncSession, course_id: int, user_id: int, role: str
) -> CourseDiscussionListResponse:
    await _ensure_course_access(db, course_id, user_id, role)
    rows = await ContentRepository(db).list_discussions(course_id)
    data = [await _discussion_item(db, discussion, full_name, email) for discussion, full_name, email in rows]
    return CourseDiscussionListResponse(data=data)


async def create_course_discussion(
    db: AsyncSession,
    course_id: int,
    payload: CourseDiscussionCreate,
    user_id: int,
    role: str,
) -> CourseDiscussionItem:
    await _ensure_course_access(db, course_id, user_id, role)
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError(f"User {user_id} not found")
    discussion = await ContentRepository(db).create_discussion(course_id, user_id, payload.message.strip())
    return await _discussion_item(db, discussion, user.full_name, user.email)
