from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

RubricLevel = Literal["pass", "merit", "distinction"]


class RubricCriterionWrite(BaseModel):
    lo_code: str = Field(..., min_length=1, max_length=30)
    lo_statement: str = Field("", max_length=2_000)
    ac_code: str = Field(..., min_length=1, max_length=30)
    level: RubricLevel
    statement: str = Field(..., min_length=1, max_length=4_000)

    @field_validator("lo_code", "ac_code", "statement", "lo_statement", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class RubricCriterionItem(RubricCriterionWrite):
    criterion_id: str


class RubricTemplateWrite(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    form_title: str | None = Field(None, max_length=255)
    qualification: str | None = Field(None, max_length=255)
    unit_code_title: str | None = Field(None, max_length=255)
    default_assignment_title: str | None = Field(None, max_length=255)
    source_file_name: str | None = Field(None, max_length=255)
    criteria: list[RubricCriterionWrite] = Field(..., min_length=1, max_length=200)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class RubricTemplateUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=255)
    form_title: str | None = Field(None, max_length=255)
    qualification: str | None = Field(None, max_length=255)
    unit_code_title: str | None = Field(None, max_length=255)
    default_assignment_title: str | None = Field(None, max_length=255)
    criteria: list[RubricCriterionWrite] | None = Field(None, min_length=1, max_length=200)
    is_archived: bool | None = None


class RubricTemplateDraft(RubricTemplateWrite):
    """A parsed Excel import that has not been saved yet."""


class RubricTemplateItem(BaseModel):
    template_id: int
    name: str
    form_title: str | None = None
    qualification: str | None = None
    unit_code_title: str | None = None
    default_assignment_title: str | None = None
    source_file_name: str | None = None
    is_archived: bool = False
    created_by: int
    created_by_name: str | None = None
    can_edit: bool = False
    criteria_count: int = 0
    criteria: list[RubricCriterionItem] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RubricTemplateListResponse(BaseModel):
    data: list[RubricTemplateItem]


class AssignmentRubricWrite(BaseModel):
    template_id: int = Field(..., gt=0)
    first_assessor_user_id: int | None = Field(None, gt=0)
    second_assessor_user_id: int | None = Field(None, gt=0)
    assignment_title: str | None = Field(None, max_length=255)
    issued_at: date | None = None


class RubricPerson(BaseModel):
    user_id: int
    name: str


class AssignmentRubricItem(BaseModel):
    assignment_id: int
    template_id: int | None = None
    template_name: str
    form_title: str | None = None
    qualification: str | None = None
    unit_code_title: str | None = None
    assignment_title: str | None = None
    issued_at: date | None = None
    criteria: list[RubricCriterionItem]
    first_assessor: RubricPerson | None = None
    second_assessor: RubricPerson | None = None
    viewer_role: Literal["first", "second", "admin", "viewer"] = "viewer"
    marking_started: bool = False


class RubricCriterionResult(BaseModel):
    criterion_id: str = Field(..., min_length=1, max_length=40)
    achieved: bool | None = None
    evidence: str = Field("", max_length=8_000)


class RubricAssessmentWrite(BaseModel):
    criteria_results: list[RubricCriterionResult] = Field(default_factory=list, max_length=200)
    actions_needed: str | None = Field(None, max_length=20_000)
    overall_comment: str | None = Field(None, max_length=20_000)
    resubmission: str | None = Field(None, max_length=100)
    assessment_date: date | None = None
    complete: bool = False


class RubricAssessmentItem(BaseModel):
    assessment_id: int | None = None
    submission_id: int
    assessor_role: Literal["first", "second"]
    assessor_user_id: int | None = None
    assessor_name: str | None = None
    criteria_results: list[RubricCriterionResult] = []
    actions_needed: str | None = None
    overall_comment: str | None = None
    resubmission: str | None = None
    assessment_date: date | None = None
    overall_grade: str | None = None
    grade_summary: dict | None = None
    status: Literal["not_started", "draft", "completed"] = "not_started"
    completed_at: datetime | None = None
    updated_at: datetime | None = None


class RubricSubmissionInfo(BaseModel):
    submission_id: int
    student_user_id: int
    student_name: str
    student_number: str = ""
    status: str
    submitted_at: datetime | None = None
    is_late: bool = False
    answer_text: str | None = None
    attachment_url: str | None = None
    attachment_name: str | None = None


class RubricMarkingResponse(BaseModel):
    rubric: AssignmentRubricItem
    submission: RubricSubmissionInfo
    grades_released: bool = False
    first: RubricAssessmentItem | None = None
    second: RubricAssessmentItem | None = None


class RubricOverviewRow(BaseModel):
    submission_id: int
    student_user_id: int
    student_name: str
    student_number: str = ""
    submission_status: str
    submitted_at: datetime | None = None
    is_late: bool = False
    needs_marking: bool = True
    first_status: str | None = None
    first_grade: str | None = None
    second_status: str | None = None
    second_grade: str | None = None


class RubricOverviewResponse(BaseModel):
    assignment_id: int
    assignment_title: str
    grades_released: bool = False
    rubric: AssignmentRubricItem
    rows: list[RubricOverviewRow]
    to_mark: int = 0
    first_completed: int = 0
    second_completed: int | None = None
    missing_first: int = 0
    missing_second: int | None = None


class StudentRubricFeedback(BaseModel):
    assignment_id: int
    assignment_title: str
    rubric: AssignmentRubricItem
    learner_name: str
    learner_id: str = ""
    submitted_at: datetime | None = None
    feedback: RubricAssessmentItem


class ClassRubricContext(BaseModel):
    class_id: int
    awarding_body: str = ""
    is_athe: bool = False
    current_user_id: int
    is_admin: bool = False
    lecturers: list[RubricPerson] = []
