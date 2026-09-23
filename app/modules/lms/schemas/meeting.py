from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MeetingStatus = Literal["scheduled", "cancelled", "completed"]
# 'google' remains readable so live classes created by the retired Google Meet
# provider can still be listed and cancelled. New classes are always Zoom.
MeetingProvider = Literal["google", "zoom"]
RecordingMode = Literal["cloud", "none"]


class MeetingOptions(BaseModel):
    """Zoom scheduling options exposed by the LMS schedule form."""

    passcode: str | None = Field(None, max_length=10)
    waiting_room: bool = False
    join_before_host: bool = True
    mute_upon_entry: bool = True
    host_video: bool = True
    participant_video: bool = False
    auto_recording: RecordingMode | None = None

    @model_validator(mode="after")
    def validate_options(self):
        if self.passcode is not None:
            passcode = self.passcode.strip()
            if passcode and not passcode.isalnum():
                raise ValueError("The passcode may contain letters and numbers only")
            self.passcode = passcode or None
        if self.waiting_room and self.join_before_host:
            # Zoom ignores join-before-host when a waiting room is enabled.
            self.join_before_host = False
        return self


class MeetingSchedule(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    start_time: datetime
    end_time: datetime
    timezone: str | None = Field(None, max_length=100)
    options: MeetingOptions = Field(default_factory=MeetingOptions)

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("start_time and end_time must include a timezone")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if (self.end_time - self.start_time).total_seconds() > 24 * 3600:
            raise ValueError("A live class cannot be longer than 24 hours")
        return self


class MeetingCreate(MeetingSchedule):
    class_id: int = Field(..., gt=0)
    provider: Literal["zoom"] = "zoom"


class MeetingUpdate(MeetingSchedule):
    pass


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
    provider: MeetingProvider = "zoom"
    join_uri: str
    provider_meeting_id: str | None = None
    processing_status: str = "not_applicable"
    processing_error: str | None = None
    students_notified: bool
    attendee_count: int
    created_at: datetime


class MeetingListResponse(BaseModel):
    data: list[MeetingItem]


class SchedulableClassItem(BaseModel):
    class_id: int
    code: str
    name: str
    course_code: str
    course_title: str
    timezone: str
    status: str
    student_count: int


class SchedulableClassListResponse(BaseModel):
    data: list[SchedulableClassItem]
