from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.lms.schemas.coursework import CourseworkAssignmentItem
from app.modules.lms.schemas.exam import ExamEditorResponse


class AssessmentTemplateCreate(BaseModel):
    kind: Literal["assignment", "practice_test"]
    title: str = Field(..., min_length=2, max_length=255)
    instructions: str = Field("Answer every question.", min_length=2, max_length=20_000)
    assignment_type: Literal["regular", "timed"] = "regular"
    submission_type: Literal["written", "multimedia", "coding"] = "written"
    duration_minutes: int | None = Field(None, ge=1, le=1440)

    @field_validator("title", "instructions")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Must contain at least 2 non-space characters")
        return value

    @model_validator(mode="after")
    def validate_kind(self):
        if self.kind == "practice_test":
            self.assignment_type = "timed"
            self.submission_type = "written"
            self.duration_minutes = self.duration_minutes or 30
        elif self.assignment_type == "timed" and self.duration_minutes is None:
            self.duration_minutes = 60
        elif self.assignment_type != "timed":
            self.duration_minutes = None
        return self


class AssessmentTemplateUpdate(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    instructions: str = Field(..., min_length=2, max_length=20_000)
    assignment_type: Literal["regular", "timed"] = "regular"
    submission_type: Literal["written", "multimedia", "coding"] = "written"
    duration_minutes: int | None = Field(None, ge=1, le=1440)

    @field_validator("title", "instructions")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Must contain at least 2 non-space characters")
        return value


class BankQuestionItem(BaseModel):
    question_id: int
    question_type: str
    prompt: str
    marks: Decimal
    position: int
    options: list[str] | None = None
    correct_option_index: int | None = None
    correct_option_indices: list[int] | None = None
    accepted_answers: list[str] | None = None


class AssessmentTemplateSummary(BaseModel):
    template_id: int
    kind: str
    title: str
    instructions: str
    assignment_type: str | None = None
    submission_type: str | None = None
    duration_minutes: int | None = None
    max_marks: Decimal
    question_count: int
    updated_at: datetime


class AssessmentTemplateDetail(AssessmentTemplateSummary):
    questions: list[BankQuestionItem]


class AssessmentTemplateListResponse(BaseModel):
    data: list[AssessmentTemplateSummary]


class TemplateApplyRequest(BaseModel):
    course_id: int = Field(..., gt=0)
    class_id: int | None = Field(None, gt=0)
    available_from: datetime | None = None
    due_at: datetime | None = None
    duration_minutes: int | None = Field(None, ge=1, le=1440)
    randomize_questions: bool = True
    randomize_options: bool = True
    status: Literal["draft", "published"] = "draft"

    @model_validator(mode="after")
    def validate_window(self):
        if self.available_from and self.due_at and self.due_at <= self.available_from:
            raise ValueError("Due time must be after the available time")
        return self


class TemplateApplyResponse(BaseModel):
    kind: Literal["assignment", "practice_test"]
    title: str
    assignment: CourseworkAssignmentItem | None = None
    exam: ExamEditorResponse | None = None
