from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CourseworkAssignmentCreate(BaseModel):
    course_id: int = Field(..., gt=0)
    target_type: Literal["course", "class"] = "course"
    target_id: int = Field(..., gt=0)
    title: str = Field(..., min_length=2, max_length=255)
    instructions: str = Field(..., min_length=2, max_length=20_000)
    assignment_type: Literal["regular", "timed"] = "regular"
    available_from: datetime | None = None
    due_at: datetime | None = None
    duration_minutes: int | None = Field(None, ge=1, le=1440)
    max_marks: Decimal = Field(Decimal("100"), gt=0, le=100_000)
    allow_late: bool = False
    status: Literal["draft", "published"] = "draft"

    @model_validator(mode="after")
    def validate_timing(self):
        if self.assignment_type == "timed" and self.duration_minutes is None:
            raise ValueError("Timed assignments require a duration")
        if self.assignment_type == "timed" and self.allow_late:
            raise ValueError("Timed assignments cannot allow late submissions")
        if self.available_from and self.due_at and self.due_at <= self.available_from:
            raise ValueError("Due time must be after the available time")
        if self.target_type == "course" and self.target_id != self.course_id:
            raise ValueError("Course-targeted assignments must use the course ID as target ID")
        return self


class CourseworkAssignmentItem(BaseModel):
    assignment_id: int
    course_id: int
    course_code: str
    course_title: str
    target_type: str
    target_id: int
    target_label: str
    title: str
    instructions: str
    assignment_type: str
    available_from: datetime | None
    due_at: datetime | None
    duration_minutes: int | None
    max_marks: Decimal
    allow_late: bool
    grades_released: bool
    status: str
    created_at: datetime
    submission_id: int | None = None
    submission_status: str | None = None
    started_at: datetime | None = None
    expires_at: datetime | None = None
    submitted_at: datetime | None = None
    marks_awarded: Decimal | None = None
    feedback: str | None = None
    answer_text: str | None = None
    attachment_asset_id: int | None = None
    attachment_url: str | None = None
    attachment_name: str | None = None
    remaining_seconds: int | None = None


class CourseworkAssignmentListResponse(BaseModel):
    data: list[CourseworkAssignmentItem]


class CourseworkDraftUpdate(BaseModel):
    answer_text: str | None = Field(None, max_length=100_000)
    attachment_asset_id: int | None = Field(None, gt=0)


class CourseworkSubmissionItem(BaseModel):
    submission_id: int
    assignment_id: int
    student_user_id: int
    student_name: str
    student_email: str
    student_number: str
    status: str
    started_at: datetime
    expires_at: datetime | None
    submitted_at: datetime | None
    answer_text: str | None
    attachment_asset_id: int | None
    attachment_url: str | None
    attachment_name: str | None
    marks_awarded: Decimal | None
    feedback: str | None
    marked_at: datetime | None


class CourseworkSubmissionListResponse(BaseModel):
    data: list[CourseworkSubmissionItem]


class CourseworkMarkUpdate(BaseModel):
    marks_awarded: Decimal = Field(..., ge=0, le=100_000)
    feedback: str | None = Field(None, max_length=20_000)


class GradeReleaseUpdate(BaseModel):
    grades_released: bool


class GradebookResultItem(BaseModel):
    assignment_id: int
    title: str
    status: str
    max_marks: Decimal
    marks_awarded: Decimal | None
    percentage: float | None
    graded: bool


class GradebookAssignmentSummary(BaseModel):
    assignment_id: int
    title: str
    target_label: str
    max_marks: Decimal
    due_at: datetime | None
    grades_released: bool
    eligible_students: int
    submitted_count: int
    graded_count: int
    average_percentage: float


class GradebookStudentItem(BaseModel):
    student_user_id: int
    student_number: str
    student_name: str
    student_email: str
    graded_count: int
    earned_marks: Decimal
    total_possible_marks: Decimal
    overall_percentage: float
    results: list[GradebookResultItem]


class GradebookSummary(BaseModel):
    student_count: int
    assignment_count: int
    submitted_count: int
    graded_count: int
    average_percentage: float


class LecturerGradebookResponse(BaseModel):
    course_id: int
    course_code: str
    course_title: str
    class_id: int | None
    class_label: str | None
    summary: GradebookSummary
    assignments: list[GradebookAssignmentSummary]
    students: list[GradebookStudentItem]


class StudentGradeItem(BaseModel):
    assignment_id: int
    title: str
    course_id: int
    course_code: str
    course_title: str
    max_marks: Decimal
    marks_awarded: Decimal
    percentage: float
    feedback: str | None
    marked_at: datetime | None


class StudentCourseGrade(BaseModel):
    course_id: int
    course_code: str
    course_title: str
    released_assignments: int
    earned_marks: Decimal
    total_possible_marks: Decimal
    overall_percentage: float
    grades: list[StudentGradeItem]


class StudentGradesResponse(BaseModel):
    overall_percentage: float
    released_assignments: int
    courses: list[StudentCourseGrade]
