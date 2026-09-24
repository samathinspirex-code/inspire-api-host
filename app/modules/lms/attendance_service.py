import csv
import math
from datetime import date, datetime, timezone
from io import StringIO

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.lms import zoom_service
from app.modules.lms.repository import AttendanceRepository, MeetingRepository
from app.modules.lms.schemas import (
    AttendanceAnalyticsResponse,
    AttendanceClassAnalytic,
    AttendanceMeetingAnalytic,
    AttendanceMonthAnalytic,
    AttendanceRecordItem,
    AttendanceRecordUpdate,
    AttendanceReportItem,
    AttendanceReportOption,
    AttendanceReportOptionsResponse,
    AttendanceReportResponse,
    AttendanceReportSummary,
    AttendanceSessionItem,
    AttendanceStudentAnalytic,
    StudentAttendanceItem,
    StudentAttendanceResponse,
    UnmatchedParticipantItem,
)

def _merge_duration(
    intervals: list[tuple[datetime, datetime]], window_start: datetime, window_end: datetime
) -> tuple[int, datetime | None, datetime | None]:
    """Attended seconds inside the class window, counting overlaps only once.

    A student who rejoins, or is connected from a phone and a laptop at the same
    time, produces overlapping sessions that must not inflate their attendance.
    """
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
        program_id=program.program_id if program else None,
        program_code=program.code if program else "",
        program_title=program.title if program else ("Orientation" if course.is_orientation else ""),
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
    db: AsyncSession, meeting_id: int, user_id: int, role: str
) -> AttendanceSessionItem:
    """Re-import attendance on demand.

    Attendance is imported automatically when Zoom reports that the meeting
    ended; this is the manual retry for when that import failed.
    """
    meeting_row = await MeetingRepository(db).get_for_organiser(meeting_id, user_id, role)
    if meeting_row is None:
        raise NotFoundError("Live class not found, or it is not one of your classes")
    meeting, _class, _course, _attendee_count = meeting_row
    if meeting.status == "cancelled":
        raise ValidationError("Cancelled live classes do not have attendance")
    if meeting.end_time > datetime.now(timezone.utc):
        raise ValidationError("Attendance can be imported after the scheduled end time")
    if meeting.provider != "zoom":
        raise ValidationError(
            "This live class was created with a retired meeting provider, so attendance cannot be imported"
        )

    await zoom_service.sync_attendance(db, meeting_id, user_id)
    repository = AttendanceRepository(db)
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


def _rate(present: int, total: int) -> float:
    return round(present * 100 / total, 2) if total else 0


async def attendance_analytics(
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
) -> AttendanceAnalyticsResponse:
    filters = _report_filters(
        user_id, role, program_id, course_id, class_id, student_user_id,
        lecturer_user_id, date_from, date_to, status, search,
    )
    repository = AttendanceRepository(db)
    summary_row = await repository.report_summary(**filters)
    total, present, absent, average_percentage, student_count, meeting_count = summary_row
    total, present, absent = int(total or 0), int(present or 0), int(absent or 0)
    grouped = await repository.report_analytics(**filters)
    return AttendanceAnalyticsResponse(
        summary=AttendanceReportSummary(
            total_records=total,
            present_count=present,
            absent_count=absent,
            present_rate=_rate(present, total),
            average_attendance_percentage=round(float(average_percentage or 0), 2),
            student_count=int(student_count or 0),
            meeting_count=int(meeting_count or 0),
        ),
        classes=[
            AttendanceClassAnalytic(
                class_id=row[0], class_code=row[1], class_name=row[2],
                course_code=row[3], course_title=row[4],
                total_records=int(row[5] or 0), present_count=int(row[6] or 0), absent_count=int(row[7] or 0),
                present_rate=_rate(int(row[6] or 0), int(row[5] or 0)),
                student_count=int(row[8] or 0), meeting_count=int(row[9] or 0),
            )
            for row in grouped["classes"]
        ],
        students=sorted(
            (
                AttendanceStudentAnalytic(
                    student_user_id=row[0], student_name=row[1], student_number=row[2], student_email=row[3],
                    total_records=int(row[4] or 0), present_count=int(row[5] or 0), absent_count=int(row[6] or 0),
                    present_rate=_rate(int(row[5] or 0), int(row[4] or 0)),
                    average_attendance_percentage=round(float(row[7] or 0), 2),
                )
                for row in grouped["students"]
            ),
            key=lambda item: (item.present_rate, item.student_name.lower()),
        ),
        months=[
            AttendanceMonthAnalytic(
                month=row[0],
                total_records=int(row[1] or 0), present_count=int(row[2] or 0), absent_count=int(row[3] or 0),
                present_rate=_rate(int(row[2] or 0), int(row[1] or 0)),
                student_count=int(row[4] or 0), meeting_count=int(row[5] or 0),
            )
            for row in grouped["months"]
        ],
        meetings=[
            AttendanceMeetingAnalytic(
                meeting_id=row[0], meeting_title=row[1], meeting_start_time=row[2],
                class_id=row[3], class_code=row[4], class_name=row[5], course_title=row[6],
                present_count=int(row[7] or 0), absent_count=int(row[8] or 0),
                present_rate=_rate(int(row[7] or 0), int(row[7] or 0) + int(row[8] or 0)),
            )
            for row in grouped["meetings"]
        ],
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
            AttendanceReportOption(value=item[0], label=f"{item[2]} · {item[1]}")
            for item in rows["courses"]
        ],
        classes=[
            AttendanceReportOption(
                value=item[0],
                label=(
                    f"{item[1]} | {item[2]} - {item[3]} "
                    f"({int(item[4] or 0)} student{'' if int(item[4] or 0) == 1 else 's'})"
                ),
            )
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
