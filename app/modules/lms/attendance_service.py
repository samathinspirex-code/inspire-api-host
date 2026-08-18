import csv
import math
from datetime import date, datetime, timezone
from io import StringIO

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.lms import integration_service
from app.modules.lms.repository import AttendanceRepository, IntegrationRepository, MeetingRepository
from app.modules.lms.schemas import (
    AttendanceRecordItem,
    AttendanceRecordUpdate,
    AttendanceReportItem,
    AttendanceReportOption,
    AttendanceReportOptionsResponse,
    AttendanceReportResponse,
    AttendanceReportSummary,
    AttendanceSessionItem,
    StudentAttendanceItem,
    StudentAttendanceResponse,
    UnmatchedParticipantItem,
)

GOOGLE_CONFERENCE_RECORDS_URL = "https://meet.googleapis.com/v2/conferenceRecords"
GOOGLE_PEOPLE_BATCH_URL = "https://people.googleapis.com/v1/people:batchGet"


def _parse_google_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _merge_duration(
    intervals: list[tuple[datetime, datetime]], window_start: datetime, window_end: datetime
) -> tuple[int, datetime | None, datetime | None]:
    clipped = []
    for start, end in intervals:
        start = max(start, window_start)
        end = min(end, window_end)
        if end > start:
            clipped.append((start, end))
    if not clipped:
        return 0, None, None

    clipped.sort(key=lambda item: item[0])
    merged = [clipped[0]]
    for start, end in clipped[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    seconds = int(sum((end - start).total_seconds() for start, end in merged))
    return seconds, merged[0][0], merged[-1][1]


def _attendance_status(attended_seconds: int, window_seconds: int, threshold: int) -> str:
    required_seconds = math.ceil(max(1, window_seconds) * threshold / 100)
    return "present" if attended_seconds >= required_seconds else "absent"


def _google_message(response: httpx.Response, fallback: str) -> str:
    try:
        detail = response.json().get("error", {}).get("message")
    except ValueError:
        detail = None
    return str(detail or fallback)


async def _get_all_pages(
    client: httpx.AsyncClient,
    url: str,
    collection_key: str,
    access_token: str,
    params: dict | None = None,
) -> list[dict]:
    output: list[dict] = []
    page_token = None
    while True:
        request_params = dict(params or {})
        if page_token:
            request_params["pageToken"] = page_token
        response = await client.get(
            url,
            params=request_params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.is_error:
            raise ValidationError(_google_message(response, "Google Meet attendance data could not be read"))
        payload = response.json()
        output.extend(payload.get(collection_key, []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            return output


async def _resolve_google_emails(
    client: httpx.AsyncClient, access_token: str, google_users: set[str]
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    resources = [f"people/{item.rsplit('/', 1)[-1]}" for item in sorted(google_users)]
    for offset in range(0, len(resources), 50):
        params: list[tuple[str, str]] = [("personFields", "emailAddresses")]
        params.extend(("resourceNames", item) for item in resources[offset : offset + 50])
        response = await client.get(
            GOOGLE_PEOPLE_BATCH_URL,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.is_error:
            raise ValidationError(
                _google_message(
                    response,
                    "Google participant identities could not be resolved. Enable the Google People API and reconnect the lecturer account.",
                )
            )
        for item in response.json().get("responses", []):
            person = item.get("person") or {}
            resource_name = item.get("requestedResourceName") or person.get("resourceName")
            emails = person.get("emailAddresses") or []
            primary = next((email for email in emails if email.get("metadata", {}).get("primary")), None)
            selected = primary or (emails[0] if emails else None)
            if resource_name and selected and selected.get("value"):
                google_id = resource_name.rsplit("/", 1)[-1]
                resolved[google_id] = selected["value"].lower()
    return resolved


def _record_item(row) -> AttendanceRecordItem:
    record, user, profile = row
    return AttendanceRecordItem(
        attendance_record_id=record.attendance_record_id,
        student_user_id=record.student_user_id,
        student_number=profile.student_number,
        full_name=user.full_name or user.email,
        email=user.email,
        status=record.status,
        attended_seconds=record.attended_seconds,
        attendance_percentage=round(record.attendance_percentage, 2),
        first_join_time=record.first_join_time,
        last_leave_time=record.last_leave_time,
        source=record.source,
        override_reason=record.override_reason,
    )


def _report_item(row) -> AttendanceReportItem:
    (
        record,
        session,
        meeting,
        class_,
        course,
        program,
        student,
        student_profile,
        lecturer,
        lecturer_profile,
    ) = row
    return AttendanceReportItem(
        attendance_record_id=record.attendance_record_id,
        meeting_id=meeting.meeting_id,
        meeting_title=meeting.title,
        meeting_start_time=meeting.start_time,
        meeting_end_time=meeting.end_time,
        program_id=program.program_id,
        program_code=program.code,
        program_title=program.title,
        course_id=course.course_id,
        course_code=course.code,
        course_title=course.title,
        class_id=class_.class_id,
        class_code=class_.code,
        class_name=class_.name,
        lecturer_user_id=lecturer.user_id,
        lecturer_staff_number=lecturer_profile.staff_number,
        lecturer_name=lecturer.full_name or lecturer.email,
        lecturer_email=lecturer.email,
        student_user_id=student.user_id,
        student_number=student_profile.student_number,
        student_name=student.full_name or student.email,
        student_email=student.email,
        status=record.status,
        attended_seconds=record.attended_seconds,
        attendance_percentage=round(record.attendance_percentage, 2),
        first_join_time=record.first_join_time,
        last_leave_time=record.last_leave_time,
        source=record.source,
        override_reason=record.override_reason,
        synced_at=session.synced_at,
    )


async def _session_item(repository: AttendanceRepository, context) -> AttendanceSessionItem:
    session, meeting, class_, course = context
    rows = await repository.list_session_records(session.attendance_session_id)
    records = [_record_item(row) for row in rows]
    return AttendanceSessionItem(
        attendance_session_id=session.attendance_session_id,
        meeting_id=meeting.meeting_id,
        meeting_title=meeting.title,
        class_id=class_.class_id,
        class_code=class_.code,
        class_name=class_.name,
        course_code=course.code,
        course_title=course.title,
        actual_start_time=session.actual_start_time,
        actual_end_time=session.actual_end_time,
        threshold_percentage=session.threshold_percentage,
        sync_status=session.sync_status,
        sync_error=session.sync_error,
        synced_at=session.synced_at,
        present_count=sum(item.status == "present" for item in records),
        absent_count=sum(item.status == "absent" for item in records),
        unmatched_participants=[UnmatchedParticipantItem(**item) for item in session.unmatched_participants],
        records=records,
    )


async def sync_meeting_attendance(
    db: AsyncSession, meeting_id: int, lecturer_user_id: int
) -> AttendanceSessionItem:
    meeting_row = await MeetingRepository(db).get_for_lecturer(meeting_id, lecturer_user_id)
    if meeting_row is None:
        raise NotFoundError("Meeting not found or it is not assigned to your lecturer profile")
    meeting, _class, _course, _attendee_count = meeting_row
    if meeting.status == "cancelled":
        raise ValidationError("Cancelled meetings do not have attendance")
    if meeting.end_time > datetime.now(timezone.utc):
        raise ValidationError("Attendance can be synchronized after the scheduled meeting end time")

    integration = await IntegrationRepository(db).get_google_settings()
    if integration is None or not integration.enabled or not integration.attendance_sync_enabled:
        raise ValidationError("Google Meet attendance synchronization is not enabled")
    threshold = integration.attendance_threshold_percentage
    repository = AttendanceRepository(db)

    try:
        access_token = await integration_service.get_google_access_token(db, lecturer_user_id)
        async with httpx.AsyncClient(timeout=30) as client:
            conferences = await _get_all_pages(
                client,
                GOOGLE_CONFERENCE_RECORDS_URL,
                "conferenceRecords",
                access_token,
                {"pageSize": 100, "filter": f'space.name = "{meeting.google_space_name}"'},
            )
            ended = [item for item in conferences if item.get("endTime")]
            if not ended:
                raise ValidationError(
                    "Google has not produced an ended conference record yet. Wait a few minutes and sync again."
                )
            conference = min(
                ended,
                key=lambda item: abs(
                    ((_parse_google_time(item.get("startTime")) or meeting.start_time) - meeting.start_time).total_seconds()
                ),
            )
            conference_start = _parse_google_time(conference.get("startTime"))
            conference_end = _parse_google_time(conference.get("endTime"))
            if conference_start is None or conference_end is None:
                raise ValidationError("Google returned incomplete conference times")

            window_start = max(conference_start, meeting.start_time)
            window_end = min(conference_end, meeting.end_time)
            if window_end <= window_start:
                window_start, window_end = conference_start, conference_end
            window_seconds = max(1, int((window_end - window_start).total_seconds()))

            participants = await _get_all_pages(
                client,
                f"https://meet.googleapis.com/v2/{conference['name']}/participants",
                "participants",
                access_token,
                {"pageSize": 250},
            )
            google_users = {
                item["signedinUser"]["user"]
                for item in participants
                if item.get("signedinUser", {}).get("user")
            }
            email_by_google_id = await _resolve_google_emails(client, access_token, google_users)

            participant_data = []
            for participant in participants:
                sessions = await _get_all_pages(
                    client,
                    f"https://meet.googleapis.com/v2/{participant['name']}/participantSessions",
                    "participantSessions",
                    access_token,
                    {"pageSize": 250},
                )
                intervals = []
                for item in sessions:
                    start = _parse_google_time(item.get("startTime"))
                    end = _parse_google_time(item.get("endTime")) or conference_end
                    if start and end:
                        intervals.append((start, end))
                seconds, first_join, last_leave = _merge_duration(
                    intervals, window_start, window_end
                )
                signed_in = participant.get("signedinUser")
                anonymous = participant.get("anonymousUser")
                phone = participant.get("phoneUser")
                google_id = signed_in.get("user", "").rsplit("/", 1)[-1] if signed_in else ""
                participant_data.append(
                    {
                        "participant_name": participant["name"],
                        "email": email_by_google_id.get(google_id),
                        "display_name": (signed_in or anonymous or phone or {}).get("displayName", "Unknown participant"),
                        "participant_type": "signed_in" if signed_in else "anonymous" if anonymous else "phone",
                        "seconds": seconds,
                        "intervals": intervals,
                        "first_join": first_join,
                        "last_leave": last_leave,
                    }
                )
    except (httpx.HTTPError, ValidationError) as exc:
        message = exc.message if isinstance(exc, ValidationError) else "Google is temporarily unavailable. Try syncing again."
        await repository.save_failed_sync(meeting, threshold, lecturer_user_id, message)
        raise ValidationError(message) from exc

    students = await repository.list_class_students(meeting.class_id, meeting.end_time)
    roster = {user.email.lower(): (user, profile) for user, profile in students}
    connection = await IntegrationRepository(db).get_google_connection(lecturer_user_id)
    lecturer_email = connection.google_email.lower() if connection else ""

    participation_by_email: dict[str, dict] = {}
    unmatched: list[dict] = []
    for item in participant_data:
        email = item["email"]
        if email and email == lecturer_email:
            continue
        if email and email in roster:
            bucket = participation_by_email.setdefault(
                email, {"intervals": [], "participant_names": []}
            )
            bucket["intervals"].extend(item["intervals"])
            bucket["participant_names"].append(item["participant_name"])
        else:
            unmatched.append(
                {
                    "display_name": item["display_name"],
                    "participant_type": item["participant_type"],
                    "attended_seconds": item["seconds"],
                }
            )

    calculated_records = []
    for email, (user, _profile) in roster.items():
        data = participation_by_email.get(email, {"intervals": [], "participant_names": []})
        seconds, first_join, last_leave = _merge_duration(
            data["intervals"], window_start, window_end
        )
        calculated_records.append(
            {
                "student_user_id": user.user_id,
                "status": _attendance_status(seconds, window_seconds, threshold),
                "attended_seconds": seconds,
                "attendance_percentage": min(100.0, round(seconds * 100 / window_seconds, 2)),
                "first_join_time": first_join,
                "last_leave_time": last_leave,
                "google_participant_name": ",".join(data["participant_names"]) or None,
            }
        )

    await repository.save_google_sync(
        meeting,
        threshold,
        conference["name"],
        conference_start,
        conference_end,
        unmatched,
        calculated_records,
        lecturer_user_id,
    )
    context = await repository.get_session_context(meeting_id)
    return await _session_item(repository, context)


async def get_meeting_attendance(
    db: AsyncSession, meeting_id: int, user_id: int, role: str
) -> AttendanceSessionItem:
    repository = AttendanceRepository(db)
    context = await repository.get_session_context(meeting_id)
    if context is None:
        raise NotFoundError("Attendance has not been synchronized for this meeting")
    _session, meeting, _class, _course = context
    if role == "LECTURER" and meeting.lecturer_user_id != user_id:
        raise ForbiddenError("You can view attendance only for your assigned meetings")
    if role == "STUDENT":
        raise ForbiddenError("Students can view attendance only through their personal attendance page")
    return await _session_item(repository, context)


async def override_attendance_record(
    db: AsyncSession,
    attendance_record_id: int,
    payload: AttendanceRecordUpdate,
    user_id: int,
    role: str,
) -> AttendanceRecordItem:
    repository = AttendanceRepository(db)
    context = await repository.get_record_context(attendance_record_id)
    if context is None:
        raise NotFoundError("Attendance record not found")
    record, _session, meeting, user, profile = context
    if role == "LECTURER" and meeting.lecturer_user_id != user_id:
        raise ForbiddenError("You can update attendance only for your assigned meetings")
    if role not in {"SUPER_ADMIN", "ADMIN", "LECTURER"}:
        raise ForbiddenError("Your LMS role cannot update attendance")
    record = await repository.override_record(record, payload.status, payload.reason.strip(), user_id)
    return _record_item((record, user, profile))


async def list_my_attendance(db: AsyncSession, student_user_id: int) -> StudentAttendanceResponse:
    rows = await AttendanceRepository(db).list_student_attendance(student_user_id)
    data = [
        StudentAttendanceItem(
            attendance_record_id=record.attendance_record_id,
            meeting_id=meeting.meeting_id,
            meeting_title=meeting.title,
            class_code=class_.code,
            class_name=class_.name,
            course_code=course.code,
            course_title=course.title,
            meeting_start_time=meeting.start_time,
            status=record.status,
            attended_seconds=record.attended_seconds,
            attendance_percentage=round(record.attendance_percentage, 2),
            source=record.source,
        )
        for record, _session, meeting, class_, course in rows
    ]
    present = sum(item.status == "present" for item in data)
    total = len(data)
    return StudentAttendanceResponse(
        total_sessions=total,
        present_count=present,
        absent_count=total - present,
        attendance_percentage=round(present * 100 / total, 2) if total else 0,
        data=data,
    )


def _report_filters(
    user_id: int,
    role: str,
    program_id: int | None,
    course_id: int | None,
    class_id: int | None,
    student_user_id: int | None,
    lecturer_user_id: int | None,
    date_from: date | None,
    date_to: date | None,
    status: str | None,
    search: str | None,
) -> dict:
    if role not in {"SUPER_ADMIN", "ADMIN", "LECTURER"}:
        raise ForbiddenError("Your LMS role cannot view attendance reports")
    if date_from and date_to and date_from > date_to:
        raise ValidationError("The attendance report start date must be before the end date")
    return {
        "lecturer_scope_user_id": user_id if role == "LECTURER" else None,
        "program_id": program_id,
        "course_id": course_id,
        "class_id": class_id,
        "student_user_id": student_user_id,
        "lecturer_user_id": lecturer_user_id,
        "date_from": date_from,
        "date_to": date_to,
        "status": status,
        "search": search,
    }


async def list_attendance_report(
    db: AsyncSession,
    user_id: int,
    role: str,
    page: int,
    size: int,
    program_id: int | None = None,
    course_id: int | None = None,
    class_id: int | None = None,
    student_user_id: int | None = None,
    lecturer_user_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    search: str | None = None,
) -> AttendanceReportResponse:
    filters = _report_filters(
        user_id,
        role,
        program_id,
        course_id,
        class_id,
        student_user_id,
        lecturer_user_id,
        date_from,
        date_to,
        status,
        search,
    )
    repository = AttendanceRepository(db)
    summary_row = await repository.report_summary(**filters)
    total, present, absent, average_percentage, student_count, meeting_count = summary_row
    rows = await repository.list_report(
        **filters,
        offset=(page - 1) * size,
        limit=size,
    )
    total = int(total or 0)
    present = int(present or 0)
    absent = int(absent or 0)
    return AttendanceReportResponse(
        page=page,
        size=size,
        total=total,
        pages=math.ceil(total / size) if total else 0,
        summary=AttendanceReportSummary(
            total_records=total,
            present_count=present,
            absent_count=absent,
            present_rate=round(present * 100 / total, 2) if total else 0,
            average_attendance_percentage=round(float(average_percentage or 0), 2),
            student_count=int(student_count or 0),
            meeting_count=int(meeting_count or 0),
        ),
        data=[_report_item(row) for row in rows],
    )


async def get_attendance_report_options(
    db: AsyncSession, user_id: int, role: str
) -> AttendanceReportOptionsResponse:
    if role not in {"SUPER_ADMIN", "ADMIN", "LECTURER"}:
        raise ForbiddenError("Your LMS role cannot view attendance reports")
    rows = await AttendanceRepository(db).report_options(
        lecturer_scope_user_id=user_id if role == "LECTURER" else None
    )
    return AttendanceReportOptionsResponse(
        programmes=[
            AttendanceReportOption(value=item[0], label=f"{item[1]} · {item[2]}")
            for item in rows["programmes"]
        ],
        courses=[
            AttendanceReportOption(value=item[0], label=f"{item[1]} · {item[2]}")
            for item in rows["courses"]
        ],
        classes=[
            AttendanceReportOption(value=item[0], label=f"{item[1]} · {item[2]}")
            for item in rows["classes"]
        ],
        lecturers=[
            AttendanceReportOption(
                value=item[0], label=f"{item[1]} · {item[2] or item[3]}"
            )
            for item in rows["lecturers"]
        ],
        students=[
            AttendanceReportOption(
                value=item[0], label=f"{item[1]} · {item[2] or item[3]}"
            )
            for item in rows["students"]
        ],
    )


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def build_attendance_report_csv(items: list[AttendanceReportItem]) -> str:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "attendance_record_id",
            "status",
            "attendance_percentage",
            "attended_seconds",
            "source",
            "override_reason",
            "first_join_time",
            "last_leave_time",
            "meeting_id",
            "meeting_title",
            "meeting_start_time",
            "meeting_end_time",
            "program_id",
            "program_code",
            "program_title",
            "course_id",
            "course_code",
            "course_title",
            "class_id",
            "class_code",
            "class_name",
            "lecturer_user_id",
            "lecturer_staff_number",
            "lecturer_name",
            "lecturer_email",
            "student_user_id",
            "student_number",
            "student_name",
            "student_email",
            "synced_at",
        ]
    )
    for item in items:
        writer.writerow(
            [
                _csv_safe(item.attendance_record_id),
                _csv_safe(item.status),
                _csv_safe(item.attendance_percentage),
                _csv_safe(item.attended_seconds),
                _csv_safe(item.source),
                _csv_safe(item.override_reason),
                _csv_safe(item.first_join_time.isoformat() if item.first_join_time else None),
                _csv_safe(item.last_leave_time.isoformat() if item.last_leave_time else None),
                _csv_safe(item.meeting_id),
                _csv_safe(item.meeting_title),
                _csv_safe(item.meeting_start_time.isoformat()),
                _csv_safe(item.meeting_end_time.isoformat()),
                _csv_safe(item.program_id),
                _csv_safe(item.program_code),
                _csv_safe(item.program_title),
                _csv_safe(item.course_id),
                _csv_safe(item.course_code),
                _csv_safe(item.course_title),
                _csv_safe(item.class_id),
                _csv_safe(item.class_code),
                _csv_safe(item.class_name),
                _csv_safe(item.lecturer_user_id),
                _csv_safe(item.lecturer_staff_number),
                _csv_safe(item.lecturer_name),
                _csv_safe(item.lecturer_email),
                _csv_safe(item.student_user_id),
                _csv_safe(item.student_number),
                _csv_safe(item.student_name),
                _csv_safe(item.student_email),
                _csv_safe(item.synced_at.isoformat() if item.synced_at else None),
            ]
        )
    return output.getvalue()


async def export_attendance_report_csv(
    db: AsyncSession,
    user_id: int,
    role: str,
    program_id: int | None = None,
    course_id: int | None = None,
    class_id: int | None = None,
    student_user_id: int | None = None,
    lecturer_user_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    search: str | None = None,
) -> str:
    filters = _report_filters(
        user_id,
        role,
        program_id,
        course_id,
        class_id,
        student_user_id,
        lecturer_user_id,
        date_from,
        date_to,
        status,
        search,
    )
    rows = await AttendanceRepository(db).list_report(**filters)
    return build_attendance_report_csv([_report_item(row) for row in rows])
