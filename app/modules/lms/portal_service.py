from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.cms.models import Program
from app.modules.lms.assignment_service import _lecturer_item, _student_item
from app.modules.lms.repository import AssignmentRepository, CourseRepository, ModuleRepository, PortalRepository
from app.modules.lms.schemas import (
    CoursePresentationUpdate,
    ModuleItem,
    PortalClassDetailResponse,
    PortalClassItem,
    PortalClassListResponse,
    PortalCourseDetailResponse,
    PortalCourseItem,
    PortalCourseListResponse,
)


def _course_item(row, role: str) -> PortalCourseItem:
    course, program_title, program_code, module_count, class_count, people_count = row
    return PortalCourseItem(
        course_id=course.course_id,
        program_id=course.program_id,
        is_orientation=bool(course.is_orientation),
        catalogue_course_id=course.catalogue_course_id,
        program_title=program_title or ("Orientation" if course.is_orientation else ""),
        program_code=program_code or "",
        code=course.code,
        title=course.title,
        description=course.description,
        takeaways=course.takeaways,
        cover_image_url=course.cover_image_url,
        status=course.status,
        created_at=course.created_at,
        updated_at=course.updated_at,
        module_count=module_count,
        class_count=class_count,
        people_count=people_count,
        people_label="Students" if role in {"LECTURER", "SUPER_ADMIN", "ADMIN"} else "Lecturers",
    )


def _class_item(row, role: str) -> PortalClassItem:
    class_, course_code, course_title, cover_image_url, program_title, people_count, is_orientation = row
    return PortalClassItem(
        class_id=class_.class_id,
        course_id=class_.course_id,
        course_code=course_code,
        course_title=course_title,
        cover_image_url=cover_image_url,
        is_orientation=bool(is_orientation),
        program_title=program_title or ("Orientation" if is_orientation else ""),
        code=class_.code,
        name=class_.name,
        description=class_.description,
        start_date=class_.start_date,
        end_date=class_.end_date,
        delivery_mode=class_.delivery_mode,
        timezone=class_.timezone,
        capacity=class_.capacity,
        status=class_.status,
        study_mode=class_.study_mode,
        created_at=class_.created_at,
        updated_at=class_.updated_at,
        people_count=people_count,
        people_label="Students" if role in {"LECTURER", "SUPER_ADMIN", "ADMIN"} else "Lecturers",
    )


async def list_my_courses(db: AsyncSession, user_id: int, role: str) -> PortalCourseListResponse:
    rows = await PortalRepository(db).list_courses(user_id, role)
    return PortalCourseListResponse(data=[_course_item(row, role) for row in rows])


async def get_my_course(
    db: AsyncSession, course_id: int, user_id: int, role: str
) -> PortalCourseDetailResponse:
    row = await PortalRepository(db).get_course(course_id, user_id, role)
    if row is None:
        raise NotFoundError("This course is not assigned to your LMS profile")
    assignments = (
        await AssignmentRepository(db).list_course_students(course_id)
        if role in {"LECTURER", "SUPER_ADMIN", "ADMIN"}
        else await AssignmentRepository(db).list_course_lecturers(course_id)
    )
    modules = await ModuleRepository(db).list_by_course(course_id)
    if role == "STUDENT":
        modules = [module for module in modules if module.status == "active"]
    people_label = "Students" if role in {"LECTURER", "SUPER_ADMIN", "ADMIN"} else "Lecturers"
    return PortalCourseDetailResponse(
        course=_course_item(row, role),
        modules=[ModuleItem.model_validate(module) for module in modules],
        people=[
            (
                _student_item(user, profile, relation, relation.status)
                if role in {"LECTURER", "SUPER_ADMIN", "ADMIN"}
                else _lecturer_item(user, profile, relation)
            )
            for user, profile, relation in assignments
        ],
        people_label=people_label,
    )


async def update_my_course_presentation(
    db: AsyncSession,
    course_id: int,
    payload: CoursePresentationUpdate,
    user_id: int,
    role: str = "LECTURER",
) -> PortalCourseDetailResponse:
    row = await PortalRepository(db).get_course(course_id, user_id, role)
    if row is None:
        raise NotFoundError("This course is not assigned to your lecturer profile")
    course = row[0]
    if not payload.is_orientation and await db.get(Program, payload.program_id) is None:
        raise NotFoundError(f"Programme {payload.program_id} not found")
    if payload.catalogue_course_id is not None and not await db.scalar(text("""
        SELECT 1 FROM academic_courses
        WHERE course_id=:course_id AND legacy_program_id=:program_id AND status <> 'archived'
    """), {"course_id": payload.catalogue_course_id, "program_id": payload.program_id}):
        raise ConflictError("The selected catalogue course does not match this template pathway")
    code = payload.code.strip().upper()
    repository = CourseRepository(db)
    if await repository.get_by_code(code, exclude_course_id=course_id) is not None:
        raise ConflictError(f"Course code '{code}' is already in use")
    await repository.update(course, {
        **payload.model_dump(),
        "code": code,
        "title": payload.title.strip(),
        "description": payload.description.strip() if payload.description else None,
        "takeaways": payload.takeaways.strip() if payload.takeaways else None,
        "cover_image_url": payload.cover_image_url.strip() if payload.cover_image_url else None,
    })
    return await get_my_course(db, course_id, user_id, role)


async def list_my_classes(db: AsyncSession, user_id: int, role: str) -> PortalClassListResponse:
    rows = await PortalRepository(db).list_classes(user_id, role)
    return PortalClassListResponse(data=[_class_item(row, role) for row in rows])


async def get_my_class(
    db: AsyncSession, class_id: int, user_id: int, role: str
) -> PortalClassDetailResponse:
    row = await PortalRepository(db).get_class(class_id, user_id, role)
    if row is None:
        raise NotFoundError("This class is not assigned to your LMS profile")
    assignments = (
        await AssignmentRepository(db).list_class_students(class_id)
        if role in {"LECTURER", "SUPER_ADMIN", "ADMIN"}
        else await AssignmentRepository(db).list_class_lecturers(class_id)
    )
    people_label = "Students" if role in {"LECTURER", "SUPER_ADMIN", "ADMIN"} else "Lecturers"
    return PortalClassDetailResponse(
        class_=_class_item(row, role),
        people=[
            (
                _student_item(user, profile, relation, "assigned")
                if role in {"LECTURER", "SUPER_ADMIN", "ADMIN"}
                else _lecturer_item(user, profile, relation)
            )
            for user, profile, relation in assignments
        ],
        people_label=people_label,
    )
