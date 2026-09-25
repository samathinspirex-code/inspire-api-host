from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.modules.lms import notification_service, zoom_service
from app.modules.lms.repository import MeetingRepository
from app.modules.lms.schemas import (
    MeetingCreate,
    MeetingItem,
    MeetingListResponse,
    MeetingScheduleResult,
    MeetingUpdate,
    SchedulableClassItem,
    SchedulableClassListResponse,
)


async def _meeting_item(db: AsyncSession, row) -> MeetingItem:
    meeting, class_, course, attendee_count = row
    repository = MeetingRepository(db)
    class_ids = await repository.audience_class_ids(meeting.meeting_id, meeting.class_id)
    attendee_count = len(await repository.list_student_emails_for_classes(class_ids))
    return MeetingItem(
        meeting_id=meeting.meeting_id,
        class_id=meeting.class_id,
        class_code=class_.code,
        class_name=class_.name,
        class_ids=class_ids,
        audience_type=getattr(meeting, "audience_type", "class"),
        audience_label=getattr(meeting, "audience_label", None),
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
    school_rows = (await db.execute(text("""
        SELECT lc.class_id, s.school_id, s.name AS school_name
        FROM lms_classes lc
        LEFT JOIN academic_courses ac ON ac.course_id=lc.academic_course_id
        LEFT JOIN academic_schools s ON s.school_id=ac.school_id
        WHERE lc.class_id = ANY(:class_ids)
    """), {"class_ids": [row[0].class_id for row in rows]})).mappings().all() if rows else []
    schools = {row["class_id"]: row for row in school_rows}
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
                school_id=schools.get(class_.class_id, {}).get("school_id"),
                school_name=schools.get(class_.class_id, {}).get("school_name"),
            )
            for class_, course, student_count in rows
        ]
    )


def _occurrence_windows(payload: MeetingCreate) -> list[tuple[datetime, datetime]]:
    """Start and end times for every session this scheduling action creates."""
    duration = payload.end_time - payload.start_time
    rule = payload.recurrence
    if rule is None:
        return [(payload.start_time, payload.end_time)]
    starts: list[datetime] = []
    if rule.frequency == "daily":
        step = timedelta(days=rule.interval)
        cursor = payload.start_time
        while len(starts) < rule.occurrences:
            starts.append(cursor)
            cursor += step
    else:
        # Weekly repeats can land on several weekdays, so walk week by week and
        # keep the chosen days in order until the requested count is reached.
        days = rule.weekdays or [payload.start_time.weekday()]
        week_start = payload.start_time - timedelta(days=payload.start_time.weekday())
        week = 0
        while len(starts) < rule.occurrences:
            for day in days:
                candidate = week_start + timedelta(weeks=week * rule.interval, days=day)
                candidate = candidate.replace(
                    hour=payload.start_time.hour, minute=payload.start_time.minute,
                    second=payload.start_time.second, microsecond=0,
                )
                if candidate < payload.start_time or len(starts) >= rule.occurrences:
                    continue
                starts.append(candidate)
            week += 1
            if week > rule.occurrences * rule.interval + 8:
                break
    return [(start, start + duration) for start in starts]


async def create_meeting(
    db: AsyncSession, payload: MeetingCreate, organiser_user_id: int, role: str
) -> MeetingScheduleResult:
    repository = MeetingRepository(db)
    class_rows = []
    for class_id in payload.class_ids:
        class_row = await repository.get_schedulable_class(class_id, organiser_user_id, role)
        if class_row is None:
            raise ValidationError("You can schedule meetings only for classes you are assigned to teach")
        class_rows.append(class_row)
    class_, course = class_rows[0]
    attendee_emails = await repository.list_student_emails_for_classes(payload.class_ids)
    if payload.audience_type == "all": audience_label = "All classes"
    elif payload.audience_type == "school":
        school = await db.scalar(text("""
            SELECT s.name FROM academic_schools s
            JOIN academic_courses ac ON ac.school_id=s.school_id
            JOIN lms_classes lc ON lc.academic_course_id=ac.course_id
            WHERE lc.class_id=:class_id
        """), {"class_id": payload.class_ids[0]})
        audience_label = school or "School meeting"
    elif len(payload.class_ids) > 1: audience_label = f"{len(payload.class_ids)} classes"
    else: audience_label = class_.name
    windows = _occurrence_windows(payload)
    created: list[MeetingItem] = []
    skipped: list[str] = []
    for start_time, end_time in windows:
        occurrence = payload.model_copy(update={"start_time": start_time, "end_time": end_time})
        try:
            provider_data = await zoom_service.create_zoom_meeting(
                db, occurrence, organiser_user_id, class_, course
            )
        except ValidationError as exc:
            # One clashing week must not discard the rest of the series.
            if not created and len(windows) == 1:
                raise
            skipped.append(f"{start_time.date().isoformat()}: {exc.message}")
            continue
        meeting = await repository.save(
            {
                "class_id": class_.class_id,
                "audience_type": payload.audience_type,
                "audience_label": audience_label,
                "lecturer_user_id": organiser_user_id,
                "title": payload.title.strip(),
                "description": (payload.description or "").strip() or None,
                "start_time": start_time,
                "end_time": end_time,
                "timezone": payload.timezone or class_.timezone,
                **provider_data,
            }, payload.class_ids
        )
        await notification_service.notify_meeting_change(db, meeting, class_, course, "created", payload.class_ids)
        created.append(await _meeting_item(db, (meeting, class_, course, len(attendee_emails))))
    if not created:
        raise ValidationError(
            "None of the repeated sessions could be scheduled. " + " ".join(skipped)
        )
    return MeetingScheduleResult(data=created, skipped=skipped)


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
    class_ids = await repository.audience_class_ids(meeting.meeting_id, meeting.class_id)
    await notification_service.notify_meeting_change(db, meeting, class_, course, "updated", class_ids)
    return await _meeting_item(db, (meeting, class_, course, attendee_count))


async def cancel_meeting(db: AsyncSession, meeting_id: int, user_id: int, role: str) -> MeetingItem:
    repository = MeetingRepository(db)
    row = await repository.get_for_organiser(meeting_id, user_id, role)
    if row is None:
        raise NotFoundError("Meeting was not found")
    meeting, class_, course, attendee_count = row
    if meeting.status == "cancelled":
        return await _meeting_item(db, row)
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
    class_ids = await repository.audience_class_ids(meeting.meeting_id, meeting.class_id)
    await notification_service.notify_meeting_change(db, meeting, class_, course, "cancelled", class_ids)
    return await _meeting_item(db, (meeting, class_, course, attendee_count))


async def list_my_meetings(db: AsyncSession, user_id: int, role: str) -> MeetingListResponse:
    rows = await MeetingRepository(db).list_for_user(user_id, role)
    return MeetingListResponse(data=[await _meeting_item(db, row) for row in rows])
