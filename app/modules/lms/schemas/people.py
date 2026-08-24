from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class ActiveUpdate(BaseModel):
    is_active: bool


class StudentCreate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    student_number: str = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=50)
    profile_image_url: str | None = Field(None, max_length=5000)
    notes: str | None = Field(None, max_length=5000)


class StudentUpdate(StudentCreate):
    pass


class StudentItem(BaseModel):
    user_id: int
    full_name: str
    email: str
    student_number: str
    phone: str | None
    profile_image_url: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    authenticator_status: Literal[
        "not_invited", "invitation_sent", "invitation_expired", "configured"
    ] = "not_invited"
    authenticator_invitation_expires_at: datetime | None = None


class StudentListResponse(BaseModel):
    data: list[StudentItem]


class LecturerCreate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    staff_number: str = Field(..., min_length=1, max_length=100)
    job_title: str | None = Field(None, max_length=150)
    phone: str | None = Field(None, max_length=50)
    profile_image_url: str | None = Field(None, max_length=5000)
    expertise: str | None = Field(None, max_length=5000)


class LecturerUpdate(LecturerCreate):
    pass


class LecturerItem(BaseModel):
    user_id: int
    full_name: str
    email: str
    staff_number: str
    job_title: str | None
    phone: str | None
    profile_image_url: str | None
    expertise: str | None
    is_active: bool
    created_at: datetime
    authenticator_status: Literal[
        "not_invited", "invitation_sent", "invitation_expired", "configured"
    ] = "not_invited"
    authenticator_invitation_expires_at: datetime | None = None


class LecturerListResponse(BaseModel):
    data: list[LecturerItem]
