from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MeetingStatus = Literal["scheduled", "cancelled", "completed"]
CalendarSyncStatus = Literal["synced", "disabled", "failed"]


class MeetingCreate(BaseModel):
    class_id: int = Field(..., gt=0)
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("start_time and end_time must include a timezone")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class MeetingUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("start_time and end_time must include a timezone")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class MeetingItem(BaseModel):
    meeting_id: int
    class_id: int
    class_code: str
    class_name: str
    course_code: str
    course_title: str
    title: str
    description: str | None
    start_time: datetime
    end_time: datetime
    timezone: str
    status: MeetingStatus
    google_meeting_uri: str
    google_meeting_code: str
    google_calendar_event_uri: str | None
    calendar_sync_status: CalendarSyncStatus
    calendar_sync_error: str | None
    students_notified: bool
    attendee_count: int
    created_at: datetime


class MeetingListResponse(BaseModel):
    data: list[MeetingItem]
