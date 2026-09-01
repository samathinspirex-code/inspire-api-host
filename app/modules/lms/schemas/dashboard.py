from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AdminDashboardMeeting(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    meeting_id: int
    title: str
    class_name: str
    course_code: str
    start_time: datetime
    end_time: datetime


class AdminDashboardCourse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    course_id: int
    code: str
    title: str
    status: str


class AdminDashboardResponse(BaseModel):
    total_students: int
    total_lecturers: int
    total_programmes: int
    active_courses: int
    active_classes: int
    published_content: int
    upcoming_classes: int
    attendance_rate: float | None
    attendance_records: int
    upcoming_meetings: list[AdminDashboardMeeting]
    recent_courses: list[AdminDashboardCourse]
    generated_at: datetime
