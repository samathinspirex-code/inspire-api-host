import httpx
from urllib.parse import quote
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError, NotFoundError, ValidationError
from app.modules.lms import integration_service
from app.modules.lms.repository import IntegrationRepository, MeetingRepository
from app.modules.lms.schemas import MeetingCreate, MeetingItem, MeetingListResponse, MeetingUpdate

GOOGLE_MEET_SPACES_URL = "https://meet.googleapis.com/v2/spaces"
GOOGLE_CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


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
        google_meeting_uri=meeting.google_meeting_uri,
        google_meeting_code=meeting.google_meeting_code,
        google_calendar_event_uri=meeting.google_calendar_event_uri,
        calendar_sync_status=meeting.calendar_sync_status,
        calendar_sync_error=meeting.calendar_sync_error,
        students_notified=meeting.students_notified,
        attendee_count=attendee_count,
        created_at=meeting.created_at,
    )


def _google_error(response: httpx.Response, fallback: str) -> str:
    try:
        message = response.json().get("error", {}).get("message")
    except ValueError:
        message = None
    return str(message or fallback)[:500]


def _calendar_event_body(payload, class_, course, meeting_uri: str, attendee_emails: list[str]) -> dict:
    return {
        "summary": payload.title.strip(),
        "description": "\n\n".join(
            part
            for part in [
                (payload.description or "").strip() or None,
                f"Inspire LMS online class: {course.code} - {class_.name}",
                f"Join Google Meet: {meeting_uri}",
            ]
            if part
        ),
        "location": meeting_uri,
        "start": {"dateTime": payload.start_time.isoformat(), "timeZone": class_.timezone},
        "end": {"dateTime": payload.end_time.isoformat(), "timeZone": class_.timezone},
        "attendees": [{"email": email} for email in attendee_emails],
        "guestsCanModify": False,
    }


async def _create_meet_space(client: httpx.AsyncClient, access_token: str, config: dict) -> dict:
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    attempts = [dict(config)]
    if "attendanceReportGenerationType" in config:
        attempts.append({key: value for key, value in config.items() if key != "attendanceReportGenerationType"})
    if "accessType" in config:
        attempts.append(
            {
                key: value
                for key, value in config.items()
                if key not in {"attendanceReportGenerationType", "accessType"}
            }
        )
    response = None
    for attempt in attempts:
        response = await client.post(GOOGLE_MEET_SPACES_URL, headers=headers, json={"config": attempt})
        if not response.is_error:
            break
    assert response is not None
    if response.is_error:
        raise ValidationError(_google_error(response, "Google Meet could not create the meeting space"))
    result = response.json()
    if not result.get("name") or not result.get("meetingUri"):
        raise ValidationError("Google Meet returned an incomplete meeting space")
    return result


async def create_meeting(
    db: AsyncSession, payload: MeetingCreate, lecturer_user_id: int
) -> MeetingItem:
    repository = MeetingRepository(db)
    class_row = await repository.get_assigned_class(payload.class_id, lecturer_user_id)
    if class_row is None:
        raise ValidationError("You can schedule meetings only for classes assigned to you")
    class_, course = class_row
    integration = await IntegrationRepository(db).get_google_settings()
    if integration is None or not integration.enabled:
        raise ValidationError("Google Meet integration is not enabled")

    access_token = await integration_service.get_google_access_token(db, lecturer_user_id)
    space_config = {
        "accessType": integration.default_access_type.upper(),
        "entryPointAccess": "ALL",
    }
    if integration.attendance_sync_enabled:
        space_config["attendanceReportGenerationType"] = "GENERATE_REPORT"

    attendee_emails = await repository.list_student_emails(payload.class_id)
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            space = await _create_meet_space(client, access_token, space_config)
            calendar_event = None
            calendar_error = None
            if integration.calendar_sync_enabled:
                event_body = _calendar_event_body(
                    payload, class_, course, space["meetingUri"], attendee_emails
                )
                calendar_response = await client.post(
                    GOOGLE_CALENDAR_EVENTS_URL,
                    params={"sendUpdates": "all"},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=event_body,
                )
                if calendar_response.is_error:
                    calendar_error = _google_error(
                        calendar_response, "Google Calendar event creation failed"
                    )
                else:
                    calendar_event = calendar_response.json()
    except httpx.HTTPError as exc:
        raise ValidationError("Google is temporarily unavailable. Please try again.") from exc

    calendar_status = (
        "disabled"
        if not integration.calendar_sync_enabled
        else "synced"
        if calendar_event
        else "failed"
    )
    meeting = await repository.save(
        {
            "class_id": class_.class_id,
            "lecturer_user_id": lecturer_user_id,
            "title": payload.title.strip(),
            "description": (payload.description or "").strip() or None,
            "start_time": payload.start_time,
            "end_time": payload.end_time,
            "timezone": class_.timezone,
            "google_space_name": space["name"],
            "google_meeting_uri": space["meetingUri"],
            "google_meeting_code": space.get("meetingCode", ""),
            "google_calendar_event_id": calendar_event.get("id") if calendar_event else None,
            "google_calendar_event_uri": calendar_event.get("htmlLink") if calendar_event else None,
            "calendar_sync_status": calendar_status,
            "calendar_sync_error": calendar_error,
            "students_notified": bool(calendar_event and attendee_emails),
        }
    )
    return _meeting_item((meeting, class_, course, len(attendee_emails)))


async def update_meeting(
    db: AsyncSession, meeting_id: int, payload: MeetingUpdate, lecturer_user_id: int
) -> MeetingItem:
    repository = MeetingRepository(db)
    row = await repository.get_for_lecturer(meeting_id, lecturer_user_id)
    if row is None:
        raise NotFoundError("Meeting was not found")
    meeting, class_, course, attendee_count = row
    if meeting.status != "scheduled":
        raise ValidationError("Only scheduled meetings can be edited")

    attendee_emails = await repository.list_student_emails(meeting.class_id)
    integration = await IntegrationRepository(db).get_google_settings()
    calendar_status = "disabled"
    calendar_error = None
    calendar_event_id = meeting.google_calendar_event_id
    calendar_event_uri = meeting.google_calendar_event_uri

    if integration is not None and integration.enabled and integration.calendar_sync_enabled:
        try:
            access_token = await integration_service.get_google_access_token(db, lecturer_user_id)
            event_body = _calendar_event_body(
                payload, class_, course, meeting.google_meeting_uri, attendee_emails
            )
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=25) as client:
                if calendar_event_id:
                    event_url = f"{GOOGLE_CALENDAR_EVENTS_URL}/{quote(calendar_event_id, safe='')}"
                    response = await client.patch(
                        event_url,
                        params={"sendUpdates": "all"},
                        headers=headers,
                        json=event_body,
                    )
                    if response.status_code == 404:
                        response = await client.post(
                            GOOGLE_CALENDAR_EVENTS_URL,
                            params={"sendUpdates": "all"},
                            headers=headers,
                            json=event_body,
                        )
                else:
                    response = await client.post(
                        GOOGLE_CALENDAR_EVENTS_URL,
                        params={"sendUpdates": "all"},
                        headers=headers,
                        json=event_body,
                    )
            if response.is_error:
                calendar_status = "failed"
                calendar_error = _google_error(response, "Google Calendar event update failed")
            else:
                calendar_event = response.json()
                calendar_status = "synced"
                calendar_event_id = calendar_event.get("id", calendar_event_id)
                calendar_event_uri = calendar_event.get("htmlLink", calendar_event_uri)
        except APIError as exc:
            calendar_status = "failed"
            calendar_error = exc.message
        except httpx.HTTPError:
            calendar_status = "failed"
            calendar_error = "Google is temporarily unavailable. The LMS meeting was still updated."

    meeting = await repository.update(
        meeting,
        {
            "title": payload.title.strip(),
            "description": (payload.description or "").strip() or None,
            "start_time": payload.start_time,
            "end_time": payload.end_time,
            "google_calendar_event_id": calendar_event_id,
            "google_calendar_event_uri": calendar_event_uri,
            "calendar_sync_status": calendar_status,
            "calendar_sync_error": calendar_error,
            "students_notified": bool(calendar_status == "synced" and attendee_emails),
        },
    )
    return _meeting_item((meeting, class_, course, attendee_count))


async def cancel_meeting(db: AsyncSession, meeting_id: int, lecturer_user_id: int) -> MeetingItem:
    repository = MeetingRepository(db)
    row = await repository.get_for_lecturer(meeting_id, lecturer_user_id)
    if row is None:
        raise NotFoundError("Meeting was not found")
    meeting, class_, course, attendee_count = row
    if meeting.status == "cancelled":
        return _meeting_item(row)
    if meeting.status == "completed":
        raise ValidationError("Completed meetings cannot be cancelled")

    integration = await IntegrationRepository(db).get_google_settings()
    calendar_status = "disabled"
    calendar_error = None
    calendar_event_uri = meeting.google_calendar_event_uri
    notified = False

    if (
        integration is not None
        and integration.enabled
        and integration.calendar_sync_enabled
        and meeting.google_calendar_event_id
    ):
        try:
            access_token = await integration_service.get_google_access_token(db, lecturer_user_id)
            event_url = (
                f"{GOOGLE_CALENDAR_EVENTS_URL}/"
                f"{quote(meeting.google_calendar_event_id, safe='')}"
            )
            async with httpx.AsyncClient(timeout=25) as client:
                response = await client.delete(
                    event_url,
                    params={"sendUpdates": "all"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if response.is_error and response.status_code != 404:
                calendar_status = "failed"
                calendar_error = _google_error(response, "Google Calendar event cancellation failed")
            else:
                calendar_status = "synced"
                calendar_event_uri = None
                notified = bool(attendee_count)
        except APIError as exc:
            calendar_status = "failed"
            calendar_error = exc.message
        except httpx.HTTPError:
            calendar_status = "failed"
            calendar_error = "Google is temporarily unavailable. The LMS meeting was still cancelled."

    meeting = await repository.update(
        meeting,
        {
            "status": "cancelled",
            "google_calendar_event_uri": calendar_event_uri,
            "calendar_sync_status": calendar_status,
            "calendar_sync_error": calendar_error,
            "students_notified": notified,
        },
    )
    return _meeting_item((meeting, class_, course, attendee_count))


async def list_my_meetings(
    db: AsyncSession, user_id: int, role: str
) -> MeetingListResponse:
    rows = await MeetingRepository(db).list_for_user(user_id, role)
    return MeetingListResponse(data=[_meeting_item(row) for row in rows])
