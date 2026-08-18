from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AttendanceStatus = Literal["present", "absent"]
AttendanceSyncStatus = Literal["pending", "synced", "failed"]


class AttendanceRecordUpdate(BaseModel):
    status: AttendanceStatus
    reason: str = Field(..., min_length=3, max_length=1000)


class AttendanceRecordItem(BaseModel):
    attendance_record_id: int
    student_user_id: int
    student_number: str
    full_name: str
    email: str
    status: AttendanceStatus
    attended_seconds: int
    attendance_percentage: float
    first_join_time: datetime | None
    last_leave_time: datetime | None
    source: Literal["google_meet", "manual_override"]
    override_reason: str | None


class UnmatchedParticipantItem(BaseModel):
    display_name: str
    participant_type: Literal["signed_in", "anonymous", "phone"]
    attended_seconds: int = 0


class AttendanceSessionItem(BaseModel):
    attendance_session_id: int
    meeting_id: int
    meeting_title: str
    class_id: int
    class_code: str
    class_name: str
    course_code: str
    course_title: str
    actual_start_time: datetime | None
    actual_end_time: datetime | None
    threshold_percentage: int
    sync_status: AttendanceSyncStatus
    sync_error: str | None
    synced_at: datetime | None
    present_count: int
    absent_count: int
    unmatched_participants: list[UnmatchedParticipantItem]
    records: list[AttendanceRecordItem]


class StudentAttendanceItem(BaseModel):
    attendance_record_id: int
    meeting_id: int
    meeting_title: str
    class_code: str
    class_name: str
    course_code: str
    course_title: str
    meeting_start_time: datetime
    status: AttendanceStatus
    attended_seconds: int
    attendance_percentage: float
    source: Literal["google_meet", "manual_override"]


class StudentAttendanceResponse(BaseModel):
    total_sessions: int
    present_count: int
    absent_count: int
    attendance_percentage: float
    data: list[StudentAttendanceItem]


class AttendanceReportItem(BaseModel):
    attendance_record_id: int
    meeting_id: int
    meeting_title: str
    meeting_start_time: datetime
    meeting_end_time: datetime
    program_id: int
    program_code: str
    program_title: str
    course_id: int
    course_code: str
    course_title: str
    class_id: int
    class_code: str
    class_name: str
    lecturer_user_id: int
    lecturer_staff_number: str
    lecturer_name: str
    lecturer_email: str
    student_user_id: int
    student_number: str
    student_name: str
    student_email: str
    status: AttendanceStatus
    attended_seconds: int
    attendance_percentage: float
    first_join_time: datetime | None
    last_leave_time: datetime | None
    source: Literal["google_meet", "manual_override"]
    override_reason: str | None
    synced_at: datetime | None


class AttendanceReportSummary(BaseModel):
    total_records: int
    present_count: int
    absent_count: int
    present_rate: float
    average_attendance_percentage: float
    student_count: int
    meeting_count: int


class AttendanceReportResponse(BaseModel):
    page: int
    size: int
    total: int
    pages: int
    summary: AttendanceReportSummary
    data: list[AttendanceReportItem]


class AttendanceReportOption(BaseModel):
    value: int
    label: str


class AttendanceReportOptionsResponse(BaseModel):
    programmes: list[AttendanceReportOption]
    courses: list[AttendanceReportOption]
    classes: list[AttendanceReportOption]
    lecturers: list[AttendanceReportOption]
    students: list[AttendanceReportOption]
