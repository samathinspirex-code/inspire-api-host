from datetime import date, datetime

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


class AdminDashboardPopularCourse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    course_id: int
    code: str
    title: str
    enrolments: int


class CoursePopulationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    course_id: int
    code: str
    title: str
    population: int
    new_enrolments_30d: int


class ClassPopulationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    class_id: int
    course_id: int
    course_code: str
    course_title: str
    code: str
    name: str
    population: int
    start_date: date
    end_date: date
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
    new_enrolments_30d: int
    enrolments_previous_30d: int
    popular_course: AdminDashboardPopularCourse | None
    popular_courses: list[AdminDashboardPopularCourse]
    upcoming_meetings: list[AdminDashboardMeeting]
    recent_courses: list[AdminDashboardCourse]
    generated_at: datetime


class StudentPopulationResponse(BaseModel):
    total_students: int
    active_course_enrolments: int
    new_enrolments_30d: int
    enrolments_previous_30d: int
    course_population: list[CoursePopulationItem]
    class_population: list[ClassPopulationItem]
    generated_at: datetime
