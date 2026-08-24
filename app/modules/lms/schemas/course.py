from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CourseStatus = Literal["draft", "active", "archived"]


class ProgrammeSummary(BaseModel):
    program_id: int
    code: str
    title: str
    level: str
    school: str
    awarding_body: str
    duration: str


class ProgrammeListResponse(BaseModel):
    data: list[ProgrammeSummary]


class CourseCreate(BaseModel):
    program_id: int = Field(..., gt=0)
    code: str = Field(..., min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    takeaways: str | None = Field(None, max_length=12000)
    cover_image_url: str | None = Field(None, max_length=5000)
    status: CourseStatus = "draft"


class CourseUpdate(CourseCreate):
    pass


class CoursePresentationUpdate(BaseModel):
    description: str | None = Field(None, max_length=5000)
    takeaways: str | None = Field(None, max_length=12000)
    cover_image_url: str | None = Field(None, max_length=5000)


class CourseItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    course_id: int
    program_id: int
    program_title: str
    program_code: str
    code: str
    title: str
    description: str | None
    takeaways: str | None
    cover_image_url: str | None
    status: CourseStatus
    created_at: datetime
    updated_at: datetime


class Pagination(BaseModel):
    page: int
    size: int
    total_items: int
    total_pages: int


class CourseListResponse(BaseModel):
    data: list[CourseItem]
    pagination: Pagination
