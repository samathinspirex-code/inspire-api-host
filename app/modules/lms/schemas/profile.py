from datetime import datetime

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
    reference_number: str
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


class RecoveryCodesRegenerateRequest(BaseModel):
    authenticator_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]
    message: str
