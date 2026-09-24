from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    program_id: int | None = Field(None, gt=0)
    catalogue_course_id: int | None = Field(None, gt=0)
    is_orientation: bool = False
    code: str = Field(..., min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    takeaways: str | None = Field(None, max_length=12000)
    cover_image_url: str | None = Field(None, max_length=5000)
    status: CourseStatus = "draft"

    @model_validator(mode="after")
    def orientation_skips_pathway(self):
        if self.is_orientation:
            self.program_id = None
            self.catalogue_course_id = None
            return self
        if self.program_id is None:
            raise ValueError("Select a programme, or tick Orientation")
        return self


class CourseUpdate(CourseCreate):
    pass


class CoursePresentationUpdate(CourseUpdate):
    """Full reusable-course editor payload used inside the course workspace."""


class CourseItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    course_id: int
    program_id: int | None = None
    is_orientation: bool = False
    catalogue_course_id: int | None = None
    program_title: str
    program_code: str
    code: str
    title: str
    description: str | None
    takeaways: str | None
    cover_image_url: str | None
    vimeo_folder_uri: str | None = None
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
