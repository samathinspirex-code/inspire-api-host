from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.cms.models import Program
from app.modules.lms.models import (
    ClassLecturer,
    ClassStudent,
    CourseEnrollment,
    CourseLecturer,
    LmsCalendarEvent,
    LmsClass,
    LmsCourse,
)
from app.modules.lms.schemas.calendar_event import (
    CalendarEventItem,
    CalendarEventListResponse,
    CalendarEventWrite,
)

MANAGERS = {"SUPER_ADMIN", "ADMIN"}


def _item(event: LmsCalendarEvent, program, class_, course_code: str | None, can_manage: bool) -> CalendarEventItem:
    return CalendarEventItem(
        event_id=event.event_id,
        title=event.title,
        description=event.description,
        location=event.location,
        start_time=event.start_time,
        end_time=event.end_time,
        audience_type=event.audience_type,
        program_id=event.program_id,
        program_code=program.code if program else None,
        program_title=program.title if program else None,
        class_id=event.class_id,
        class_code=class_.code if class_ else None,
        class_name=class_.name if class_ else None,
        course_code=course_code,
        status=event.status,
        can_manage=can_manage,
    )


async def _scope_ids(db: AsyncSession, user_id: int, role: str) -> tuple[set[int], set[int]] | None:
    if role in MANAGERS:
        return None
    if role == "STUDENT":
        class_ids = set((await db.execute(
            select(ClassStudent.class_id).where(ClassStudent.student_user_id == user_id)
        )).scalars().all())
        program_ids = set((await db.execute(
            select(LmsCourse.program_id)
            .join(CourseEnrollment, CourseEnrollment.course_id == LmsCourse.course_id)
            .where(
                CourseEnrollment.student_user_id == user_id,
                CourseEnrollment.status == "enrolled",
                LmsCourse.program_id.is_not(None),
            )
        )).scalars().all())
        if class_ids:
            program_ids.update((await db.execute(
                select(LmsCourse.program_id)
                .join(LmsClass, LmsClass.course_id == LmsCourse.course_id)
                .where(LmsClass.class_id.in_(class_ids), LmsCourse.program_id.is_not(None))
            )).scalars().all())
        return class_ids, program_ids
    class_ids = set((await db.execute(
        select(ClassLecturer.class_id).where(ClassLecturer.lecturer_user_id == user_id)
    )).scalars().all())
    course_ids = set((await db.execute(
        select(CourseLecturer.course_id).where(CourseLecturer.lecturer_user_id == user_id)
    )).scalars().all())
    if class_ids:
        course_ids.update((await db.execute(
            select(LmsClass.course_id).where(LmsClass.class_id.in_(class_ids))
        )).scalars().all())
    program_ids = set()
    if course_ids:
        program_ids = set((await db.execute(
            select(LmsCourse.program_id).where(
                LmsCourse.course_id.in_(course_ids),
                LmsCourse.program_id.is_not(None),
            )
        )).scalars().all())
    return class_ids, program_ids


def _can_manage(event: LmsCalendarEvent, user_id: int, role: str, class_ids: set[int] | None) -> bool:
    if role in MANAGERS:
        return True
    if event.created_by == user_id:
        return True
    return event.audience_type == "class" and class_ids is not None and event.class_id in class_ids


async def list_events(db: AsyncSession, user_id: int, role: str) -> CalendarEventListResponse:
    scope = await _scope_ids(db, user_id, role)
    stmt = (
        select(LmsCalendarEvent, Program, LmsClass, LmsCourse.code)
        .outerjoin(Program, Program.program_id == LmsCalendarEvent.program_id)
        .outerjoin(LmsClass, LmsClass.class_id == LmsCalendarEvent.class_id)
        .outerjoin(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
        # Exclude cancelled events — they should not appear on any calendar view.
        .where(LmsCalendarEvent.status != "cancelled")
        .order_by(LmsCalendarEvent.start_time, LmsCalendarEvent.event_id)
    )
    managed_classes = None if scope is None else scope[0]
    if scope is not None:
        class_ids, program_ids = scope
        filters = [LmsCalendarEvent.audience_type == "university"]
        if program_ids:
            filters.append(and_(
                LmsCalendarEvent.audience_type == "programme",
                LmsCalendarEvent.program_id.in_(program_ids),
            ))
        if class_ids:
            filters.append(and_(
                LmsCalendarEvent.audience_type == "class",
                LmsCalendarEvent.class_id.in_(class_ids),
            ))
        stmt = stmt.where(or_(*filters))
    rows = (await db.execute(stmt)).all()
    return CalendarEventListResponse(data=[
        _item(event, program, class_, course_code, _can_manage(event, user_id, role, managed_classes))
        for event, program, class_, course_code in rows
    ])


async def _assert_audience(db: AsyncSession, payload: CalendarEventWrite, user_id: int, role: str) -> None:
    if payload.audience_type == "university" and role not in MANAGERS:
        raise ForbiddenError("Only an administrator can add an event for the whole university")
    if payload.audience_type == "programme":
        program = await db.get(Program, payload.program_id)
        if program is None:
            raise ValidationError("The selected programme was not found")
        if role not in MANAGERS:
            scope = await _scope_ids(db, user_id, role)
            if scope is None or payload.program_id not in scope[1]:
                raise ForbiddenError("You can add programme events only for a programme you teach")
    if payload.audience_type == "class":
        class_ = await db.get(LmsClass, payload.class_id)
        if class_ is None or class_.status == "cancelled":
            raise ValidationError("The selected class was not found")
        if role not in MANAGERS:
            scope = await _scope_ids(db, user_id, role)
            if scope is None or payload.class_id not in scope[0]:
                course_link = await db.scalar(select(CourseLecturer.lecturer_user_id).where(
                    CourseLecturer.course_id == class_.course_id,
                    CourseLecturer.lecturer_user_id == user_id,
                ))
                if course_link is None:
                    raise ForbiddenError("You can add class events only for a class you teach")


async def _load(db: AsyncSession, event_id: int) -> LmsCalendarEvent:
    event = await db.get(LmsCalendarEvent, event_id)
    if event is None:
        raise NotFoundError("Calendar event not found")
    return event


async def _response(db: AsyncSession, event: LmsCalendarEvent, user_id: int, role: str) -> CalendarEventItem:
    listed = await list_events(db, user_id, role)
    match = next((item for item in listed.data if item.event_id == event.event_id), None)
    if match is None:
        raise ForbiddenError("You cannot view this calendar event")
    return match


async def create_event(
    db: AsyncSession, payload: CalendarEventWrite, user_id: int, role: str,
) -> CalendarEventItem:
    await _assert_audience(db, payload, user_id, role)
    event = LmsCalendarEvent(**payload.model_dump(), created_by=user_id, status="scheduled")
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return await _response(db, event, user_id, role)


async def _managed_classes(db: AsyncSession, user_id: int, role: str) -> set[int] | None:
    scope = await _scope_ids(db, user_id, role)
    return None if scope is None else scope[0]


async def update_event(
    db: AsyncSession, event_id: int, payload: CalendarEventWrite, user_id: int, role: str,
) -> CalendarEventItem:
    event = await _load(db, event_id)
    if not _can_manage(event, user_id, role, await _managed_classes(db, user_id, role)):
        raise ForbiddenError("You cannot change this calendar event")
    await _assert_audience(db, payload, user_id, role)
    for field, value in payload.model_dump().items():
        setattr(event, field, value)
    await db.commit()
    await db.refresh(event)
    return await _response(db, event, user_id, role)


async def cancel_event(db: AsyncSession, event_id: int, user_id: int, role: str) -> CalendarEventItem:
    event = await _load(db, event_id)
    if not _can_manage(event, user_id, role, await _managed_classes(db, user_id, role)):
        raise ForbiddenError("You cannot cancel this calendar event")
    event.status = "cancelled"
    await db.commit()
    await db.refresh(event)
    return await _response(db, event, user_id, role)
