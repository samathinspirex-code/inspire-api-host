from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class MyProfileUpdate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255)
    preferred_name: str | None = Field(None, max_length=150)
    phone: str | None = Field(None, max_length=50)
    bio: str | None = Field(None, max_length=5000)
    address: str | None = Field(None, max_length=1000)
    city: str | None = Field(None, max_length=120)
    country: str | None = Field(None, max_length=120)
    expertise: str | None = Field(None, max_length=5000)
    emergency_contact_name: str | None = Field(None, max_length=150)
    emergency_contact_phone: str | None = Field(None, max_length=50)


class ProfileStatistics(BaseModel):
    courses: int = 0
    classes: int = 0
    attendance_percentage: float | None = None
    grade_average: float | None = None
    course_progress: float | None = None
    completed_materials: int = 0
    upcoming_deadlines: int = 0
    upcoming_classes: int = 0
    students: int = 0
    unmarked_submissions: int = 0


class ProfileUpcomingItem(BaseModel):
    item_type: str
    title: str
    subtitle: str
    scheduled_at: datetime
    action_view: str


class MyProfileResponse(BaseModel):
    user_id: int
    role: str
    email: str
    full_name: str
    reference_number: str | None = None
    reference_label: str
    job_title: str | None = None
    preferred_name: str | None = None
    phone: str | None = None
    profile_image_url: str | None = None
    bio: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = None
    expertise: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    profile_completeness: int
    authenticator_enabled: bool
    recovery_codes_remaining: int
    statistics: ProfileStatistics
    upcoming: list[ProfileUpcomingItem]


class StudentAcademicCourse(BaseModel):
    course_id: int
    course_code: str
    course_title: str
    status: str
    completion_percent: float
    completed_items: int
    total_items: int
    last_activity_at: datetime | None = None


class StudentAcademicClass(BaseModel):
    class_id: int
    course_id: int
    class_code: str
    class_name: str
    course_code: str
    course_title: str
    status: str
    start_date: date
    end_date: date


class StudentAcademicAssessment(BaseModel):
    assessment_id: int
    kind: str
    title: str
    course_code: str
    course_title: str
    class_name: str | None = None
    status: str
    marks_awarded: Decimal | None = None
    max_marks: Decimal
    percentage: float | None = None
    feedback: str | None = None
    submitted_at: datetime | None = None
    due_at: datetime | None = None
    grades_released: bool = False


class StudentAcademicAttendance(BaseModel):
    attendance_record_id: int
    meeting_title: str
    course_code: str
    class_name: str
    started_at: datetime
    status: str
    attendance_percentage: float
    attended_seconds: int


class StudentAcademicActivity(BaseModel):
    learning_item_id: int
    item_title: str
    item_type: str
    course_code: str
    section_title: str
    completion_percent: float
    is_completed: bool
    last_activity_at: datetime


class StudentAcademicProfileResponse(BaseModel):
    user_id: int
    full_name: str
    preferred_name: str | None = None
    email: str
    student_number: str | None = None
    profile_image_url: str | None = None
    phone: str | None = None
    city: str | None = None
    country: str | None = None
    bio: str | None = None
    last_activity_at: datetime | None = None
    course_progress: float | None = None
    attendance_percentage: float | None = None
    assignment_average: float | None = None
    practice_average: float | None = None
    courses: list[StudentAcademicCourse]
    classes: list[StudentAcademicClass]
    assignments: list[StudentAcademicAssessment]
    practice_tests: list[StudentAcademicAssessment]
    question_papers: list[StudentAcademicAssessment]
    attendance: list[StudentAcademicAttendance]
    recent_activity: list[StudentAcademicActivity]


class RecoveryCodesRegenerateRequest(BaseModel):
    authenticator_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]
    message: str
