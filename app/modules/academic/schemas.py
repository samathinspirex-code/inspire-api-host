from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, EmailStr, Field, model_validator


Status = Literal["active", "archived"]
StudyMode = Literal["full_time", "part_time"]
CourseContentItem = Annotated[str, Field(min_length=1, max_length=255)]


class NamedNodeCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    status: Status = "active"
    position: int = Field(0, ge=0)


class AcademicLevelCreate(NamedNodeCreate):
    rank: int | None = Field(None, ge=0)


class ProgrammeLevelUpdate(BaseModel):
    level_ids: list[int] = Field(default_factory=list)


class StudyOptionUpsert(BaseModel):
    study_mode: StudyMode
    price: int = Field(..., ge=0)
    duration: str = Field(..., min_length=1, max_length=100)
    is_enabled: bool = True


class CourseCreate(BaseModel):
    programme_id: int = Field(..., gt=0)
    school_id: int = Field(..., gt=0)
    slug: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=255)
    awarding_body: str = Field(..., min_length=1, max_length=100)
    entry_requirements: str = Field(..., min_length=1, max_length=5000)
    progression_route: str = Field(..., min_length=1, max_length=5000)
    blurb: str = Field(..., min_length=1)
    image_url: str | None = None
    status: Status = "active"
    popularity: int = Field(0, ge=0, le=100)
    topics: list[CourseContentItem] = Field(default_factory=list, max_length=100)
    outcomes: list[CourseContentItem] = Field(default_factory=list, max_length=100)
    study_options: list[StudyOptionUpsert] = Field(default_factory=list)

    @model_validator(mode="after")
    def study_options_share_commercial_terms(self):
        if self.study_options:
            prices = {option.price for option in self.study_options}
            durations = {option.duration.strip().casefold() for option in self.study_options}
            if len(prices) > 1 or len(durations) > 1:
                raise ValueError("Full-time and Part-time must use the same price and duration")
            if not any(option.is_enabled for option in self.study_options):
                raise ValueError("Enable Full-time, Part-time, or both")
        return self


class ProgrammeEnrolmentCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    phone: str = Field(..., min_length=5, max_length=50)
    programme_id: int = Field(..., gt=0)
    preferred_school_id: int | None = Field(None, gt=0)
    preferred_course_id: int | None = Field(None, gt=0)
    preferred_study_mode: StudyMode | None = None


class PathwayConfirmRequest(BaseModel):
    course_id: int = Field(..., gt=0)
    study_mode: StudyMode
    class_id: int | None = Field(None, gt=0)


class TemplateDraftRequest(BaseModel):
    course_id: int = Field(..., gt=0)
    study_mode: StudyMode
    title: str = Field(..., min_length=1, max_length=255)
    snapshot: dict[str, Any] = Field(default_factory=dict)


class TemplatePublishRequest(BaseModel):
    snapshot: dict[str, Any] | None = None


class ClassFromTemplateRequest(BaseModel):
    template_id: int = Field(..., gt=0)
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    start_date: date
    end_date: date
    delivery_mode: Literal["online", "hybrid", "on_site"] = "online"
    timezone: str = Field("Asia/Colombo", min_length=1, max_length=100)
    capacity: int = Field(50, ge=1, le=1000)

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class ClassFromCourseRequest(BaseModel):
    source_course_id: int = Field(..., gt=0)
    academic_course_id: int = Field(..., gt=0)
    study_mode: StudyMode
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    start_date: date
    end_date: date
    delivery_mode: Literal["online", "hybrid", "on_site"] = "online"
    timezone: str = Field("Asia/Colombo", min_length=1, max_length=100)
    capacity: int = Field(50, ge=1, le=1000)
    status: Literal["planned", "active"] = "planned"

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class ClassStatusUpdate(BaseModel):
    status: Literal["planned", "active"]


class ClassDetailsUpdate(BaseModel):
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    start_date: date
    end_date: date
    delivery_mode: Literal["online", "hybrid", "on_site"] = "online"
    study_mode: StudyMode
    timezone: str = Field("Asia/Colombo", min_length=1, max_length=100)
    capacity: int = Field(50, ge=1, le=1000)
    status: Literal["planned", "active", "completed"] = "planned"

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class WorkspaceUpdate(BaseModel):
    snapshot: dict[str, Any]


class WorkspaceSyncRequest(BaseModel):
    template_version_id: int = Field(..., gt=0)
    template_keys: list[str] = Field(default_factory=list)


class AcademicResponse(BaseModel):
    data: Any


class ProgrammeEnrolmentResponse(BaseModel):
    enrolment_id: int
    user_id: int
    status: str
    account_created: bool
    invitation_sent: bool = False
    message: str
