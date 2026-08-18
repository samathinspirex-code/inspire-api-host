from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.lms.schemas.course import Pagination

ClassStatus = Literal["planned", "active", "completed", "cancelled"]
DeliveryMode = Literal["online", "hybrid", "on_site"]


class ClassCreate(BaseModel):
    course_id: int = Field(..., gt=0)
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    start_date: date
    end_date: date
    delivery_mode: DeliveryMode = "online"
    timezone: str = Field("Asia/Colombo", min_length=1, max_length=100)
    capacity: int = Field(50, ge=1, le=1000)
    status: ClassStatus = "planned"

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class ClassUpdate(ClassCreate):
    pass


class ClassItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    class_id: int
    course_id: int
    course_code: str
    course_title: str
    program_title: str
    code: str
    name: str
    description: str | None
    start_date: date
    end_date: date
    delivery_mode: DeliveryMode
    timezone: str
    capacity: int
    status: ClassStatus
    created_at: datetime
    updated_at: datetime


class ClassListResponse(BaseModel):
    data: list[ClassItem]
    pagination: Pagination
