from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.modules.lms import notification_service, zoom_service
from app.modules.lms.repository import MeetingRepository
from app.modules.lms.schemas import (
    MeetingCreate,
    MeetingItem,
    MeetingListResponse,
    MeetingUpdate,
    SchedulableClassItem,
    SchedulableClassListResponse,
)


def _meeting_item(row) -> MeetingItem:
    meeting, class_, course, attendee_count = row
    return MeetingItem(
        meeting_id=meeting.meeting_id,
        class_id=meeting.class_id,
        class_code=class_.code,
        class_name=class_.name,
        course_code=course.code,
        course_title=course.title,
        title=meeting.title,
        description=meeting.description,
        start_time=meeting.start_time,
        end_time=meeting.end_time,
        timezone=meeting.timezone,
        status=meeting.status,
        provider=meeting.provider,
        join_uri=meeting.join_uri,
        provider_meeting_id=meeting.provider_meeting_id,
        processing_status=meeting.processing_status,
        processing_error=meeting.processing_error,
        students_notified=meeting.students_notified,
        attendee_count=attendee_count,
        created_at=meeting.created_at,
    )


async def list_schedulable_classes(
    db: AsyncSession, user_id: int, role: str
) -> SchedulableClassListResponse:
    rows = await MeetingRepository(db).list_schedulable_classes(user_id, role)
    return SchedulableClassListResponse(
        data=[
            SchedulableClassItem(
                class_id=class_.class_id,
                code=class_.code,
                name=class_.name,
                course_code=course.code,
                course_title=course.title,
                timezone=class_.timezone,
                status=class_.status,
                student_count=student_count,
            )
            for class_, course, student_count in rows
        ]
    )


async def create_meeting(
    db: AsyncSession, payload: MeetingCreate, organiser_user_id: int, role: str
) -> MeetingItem:
    repository = MeetingRepository(db)
    class_row = await repository.get_schedulable_class(payload.class_id, organiser_user_id, role)
    if class_row is None:
        raise ValidationError(
            "You can schedule live classes only for classes you are assigned to teach"
        )
    class_, course = class_row
    attendee_emails = await repository.list_student_emails(payload.class_id)
    provider_data = await zoom_service.create_zoom_meeting(
        db, payload, organiser_user_id, class_, course
    )
    meeting = await repository.save(
        {
            "class_id": class_.class_id,
            "lecturer_user_id": organiser_user_id,
            "title": payload.title.strip(),
            "description": (payload.description or "").strip() or None,
            "start_time": payload.start_time,
            "end_time": payload.end_time,
            "timezone": payload.timezone or class_.timezone,
            **provider_data,
        }
    )
    await notification_service.notify_meeting_change(db, meeting, class_, course, "created")
    return _meeting_item((meeting, class_, course, len(attendee_emails)))


async def update_meeting(
    db: AsyncSession, meeting_id: int, payload: MeetingUpdate, user_id: int, role: str
) -> MeetingItem:
    repository = MeetingRepository(db)
    row = await repository.get_for_organiser(meeting_id, user_id, role)
    if row is None:
        raise NotFoundError("Meeting was not found")
    meeting, class_, course, attendee_count = row
    if meeting.status != "scheduled":
        raise ValidationError("Only scheduled live classes can be edited")
    if meeting.provider != "zoom":
        raise ValidationError(
            "This class was created with a retired meeting provider and can only be cancelled"
        )

    meeting = await repository.update(
        meeting, await zoom_service.update_zoom_meeting(db, meeting, payload)
    )
    await notification_service.notify_meeting_change(db, meeting, class_, course, "updated")
    return _meeting_item((meeting, class_, course, attendee_count))


async def cancel_meeting(db: AsyncSession, meeting_id: int, user_id: int, role: str) -> MeetingItem:
    repository = MeetingRepository(db)
    row = await repository.get_for_organiser(meeting_id, user_id, role)
    if row is None:
        raise NotFoundError("Meeting was not found")
    meeting, class_, course, attendee_count = row
    if meeting.status == "cancelled":
        return _meeting_item(row)
    if meeting.status == "completed":
        raise ValidationError("Completed live classes cannot be cancelled")

    # Meetings created by the retired Google provider are cancelled locally only;
    # there is no remote meeting left to delete.
    if meeting.provider == "zoom":
        await zoom_service.cancel_zoom_meeting(db, meeting)
    meeting = await repository.update(
        meeting,
        {"status": "cancelled", "processing_status": "cancelled", "processing_error": None},
    )
    await notification_service.notify_meeting_change(db, meeting, class_, course, "cancelled")
    return _meeting_item((meeting, class_, course, attendee_count))


async def list_my_meetings(db: AsyncSession, user_id: int, role: str) -> MeetingListResponse:
    rows = await MeetingRepository(db).list_for_user(user_id, role)
    return MeetingListResponse(data=[_meeting_item(row) for row in rows])
