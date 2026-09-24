from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


AudienceType = Literal["university", "programme", "class"]


class CalendarEventWrite(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    location: str | None = Field(None, max_length=255)
    start_time: datetime
    end_time: datetime
    audience_type: AudienceType
    program_id: int | None = Field(None, gt=0)
    class_id: int | None = Field(None, gt=0)

    @model_validator(mode="after")
    def check_audience(self):
        if self.end_time <= self.start_time:
            raise ValueError("End time must be after the start time")
        if self.audience_type == "university":
            self.program_id = None
            self.class_id = None
        elif self.audience_type == "programme":
            if self.program_id is None:
                raise ValueError("Choose a programme")
            self.class_id = None
        else:
            if self.class_id is None:
                raise ValueError("Choose a class")
            self.program_id = None
        if self.description is not None:
            self.description = self.description.strip() or None
        if self.location is not None:
            self.location = self.location.strip() or None
        self.title = self.title.strip()
        return self


class CalendarEventItem(BaseModel):
    event_id: int
    title: str
    description: str | None
    location: str | None
    start_time: datetime
    end_time: datetime
    audience_type: AudienceType
    program_id: int | None
    program_code: str | None
    program_title: str | None
    class_id: int | None
    class_code: str | None
    class_name: str | None
    course_code: str | None
    status: Literal["scheduled", "cancelled"]
    can_manage: bool


class CalendarEventListResponse(BaseModel):
    data: list[CalendarEventItem]
