from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ExamCreate(BaseModel):
    course_id: int = Field(..., gt=0)
    target_type: Literal["course", "class"] = "course"
    target_id: int = Field(..., gt=0)
    title: str = Field(..., min_length=2, max_length=255)
    instructions: str = Field(..., min_length=2, max_length=20_000)
    available_from: datetime | None = None
    due_at: datetime | None = None
    duration_minutes: int = Field(..., ge=1, le=1440)
    randomize_questions: bool = True
    randomize_options: bool = True

    @field_validator("title", "instructions")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Must contain at least 2 non-space characters")
        return value

    @model_validator(mode="after")
    def validate_exam(self):
        if self.available_from and self.due_at and self.due_at <= self.available_from:
            raise ValueError("Due time must be after the available time")
        if self.target_type == "course" and self.target_id != self.course_id:
            raise ValueError("Course-targeted exams must use the course ID as target ID")
        return self


class PracticeTestCreate(BaseModel):
    target_type: Literal["course", "class"] = "course"
    target_id: int | None = Field(None, gt=0)
    title: str = Field(..., min_length=2, max_length=255)
    instructions: str = Field("Answer every question and submit when finished.", min_length=2, max_length=20_000)
    available_from: datetime | None = None
    due_at: datetime | None = None
    duration_minutes: int = Field(30, ge=1, le=1440)
    randomize_questions: bool = True
    randomize_options: bool = True

    @field_validator("title", "instructions")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Must contain at least 2 non-space characters")
        return value

    @model_validator(mode="after")
    def validate_practice_test(self):
        if self.available_from and self.due_at and self.due_at <= self.available_from:
            raise ValueError("Due time must be after the available time")
        return self


class ExamScheduleUpdate(BaseModel):
    available_from: datetime | None = None
    due_at: datetime | None = None
    duration_minutes: int = Field(..., ge=1, le=1440)

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.available_from and self.due_at and self.due_at <= self.available_from:
            raise ValueError("Deadline must be after the available time")
        return self


class ExamQuestionUpsert(BaseModel):
    question_type: Literal["mcq", "multiple_answer", "short_answer", "essay"]
    prompt: str = Field(..., min_length=2, max_length=20_000)
    marks: Decimal = Field(..., gt=0, le=100_000)
    position: int = Field(1, ge=1)
    options: list[str] | None = None
    correct_option_index: int | None = Field(None, ge=0)
    correct_option_indices: list[int] | None = None
    accepted_answers: list[str] | None = None

    @field_validator("prompt")
    @classmethod
    def strip_prompt(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Question must contain at least 2 non-space characters")
        return value

    @model_validator(mode="after")
    def validate_answer_configuration(self):
        if self.question_type in {"mcq", "multiple_answer"}:
            if not self.options or len(self.options) < 2:
                raise ValueError("MCQ questions need at least two options")
            cleaned = [item.strip() for item in self.options]
            if any(not item for item in cleaned) or len({item.casefold() for item in cleaned}) != len(cleaned):
                raise ValueError("MCQ options must be non-empty and unique")
            if self.question_type == "mcq" and (self.correct_option_index is None or self.correct_option_index >= len(cleaned)):
                raise ValueError("Choose a valid correct MCQ option")
            if self.question_type == "multiple_answer" and (not self.correct_option_indices or any(i < 0 or i >= len(cleaned) for i in self.correct_option_indices)):
                raise ValueError("Choose at least one valid correct option")
            self.options = cleaned
        else:
            self.options = None
            self.correct_option_index = None
            self.correct_option_indices = None
        if self.question_type == "mcq": self.correct_option_indices = None
        if self.question_type == "short_answer" and self.accepted_answers:
            self.accepted_answers = [item.strip() for item in self.accepted_answers if item.strip()]
        else:
            self.accepted_answers = None
        return self


class ExamQuestionImportRequest(BaseModel):
    raw_text: str = Field(..., min_length=10, max_length=200_000)
    marks_per_question: Decimal = Field(Decimal("1"), gt=0, le=100_000)


class ExamQuestionEditorItem(BaseModel):
    question_id: int
    exam_id: int
    question_type: str
    prompt: str
    marks: Decimal
    position: int
    options: list[str] | None
    correct_option_index: int | None
    correct_option_indices: list[int] | None = None
    accepted_answers: list[str] | None


class ExamItem(BaseModel):
    exam_id: int
    assessment_kind: str = "exam"
    learning_item_id: int | None = None
    assignment_id: int
    course_id: int
    course_code: str
    course_title: str
    target_type: str
    target_id: int
    target_label: str
    title: str
    instructions: str
    available_from: datetime | None
    due_at: datetime | None
    duration_minutes: int
    randomize_questions: bool
    randomize_options: bool
    max_marks: Decimal
    question_count: int
    grades_released: bool
    status: str
    attempt_id: int | None = None
    attempt_status: str | None = None
    started_at: datetime | None = None
    expires_at: datetime | None = None
    submitted_at: datetime | None = None
    remaining_seconds: int | None = None
    total_marks: Decimal | None = None
    feedback: str | None = None


class ExamListResponse(BaseModel):
    data: list[ExamItem]


class ExamEditorResponse(BaseModel):
    exam: ExamItem
    questions: list[ExamQuestionEditorItem]


class ExamStatusUpdate(BaseModel):
    status: Literal["draft", "published", "closed"]


class ExamGradeReleaseUpdate(BaseModel):
    grades_released: bool


class ExamAttemptQuestion(BaseModel):
    question_id: int
    position: int
    question_type: str
    prompt: str
    marks: Decimal
    options: list[str] | None
    selected_option_index: int | None = None
    selected_option_indices: list[int] | None = None
    answer_text: str | None = None


class ExamAttemptResponse(BaseModel):
    attempt_id: int
    exam_id: int
    title: str
    instructions: str
    status: str
    started_at: datetime
    expires_at: datetime
    submitted_at: datetime | None
    remaining_seconds: int
    questions: list[ExamAttemptQuestion]


class ExamAnswerUpdate(BaseModel):
    question_id: int = Field(..., gt=0)
    selected_option_index: int | None = Field(None, ge=0)
    selected_option_indices: list[int] | None = None
    answer_text: str | None = Field(None, max_length=100_000)

    @model_validator(mode="after")
    def validate_multiple_answer_indices(self):
        if self.selected_option_indices is not None:
            if any(index < 0 for index in self.selected_option_indices):
                raise ValueError("Selected option indices cannot be negative")
            self.selected_option_indices = sorted(set(self.selected_option_indices))
        return self


class ExamAnswersUpdate(BaseModel):
    answers: list[ExamAnswerUpdate] = Field(default_factory=list, max_length=500)


class ExamReviewAnswerItem(BaseModel):
    question_id: int
    question_type: str
    prompt: str
    marks: Decimal
    options: list[str] | None
    correct_option_index: int | None
    selected_option_index: int | None
    answer_text: str | None
    is_correct: bool | None
    auto_marks: Decimal
    manual_marks: Decimal | None
    feedback: str | None


class ExamAttemptReviewItem(BaseModel):
    attempt_id: int
    exam_id: int
    student_user_id: int
    student_name: str
    student_email: str
    student_number: str
    status: str
    started_at: datetime
    expires_at: datetime
    submitted_at: datetime | None
    auto_marks: Decimal
    manual_marks: Decimal | None
    total_marks: Decimal | None
    max_marks: Decimal
    feedback: str | None
    answers: list[ExamReviewAnswerItem]


class ExamAttemptReviewListResponse(BaseModel):
    data: list[ExamAttemptReviewItem]


class ExamAnswerMark(BaseModel):
    question_id: int = Field(..., gt=0)
    marks_awarded: Decimal = Field(..., ge=0, le=100_000)
    feedback: str | None = Field(None, max_length=20_000)


class ExamAttemptMarkUpdate(BaseModel):
    answers: list[ExamAnswerMark] = Field(default_factory=list, max_length=500)
    feedback: str | None = Field(None, max_length=20_000)


class ExamResultAnswer(BaseModel):
    question_id: int
    prompt: str
    question_type: str
    marks: Decimal
    marks_awarded: Decimal
    feedback: str | None
    is_correct: bool | None


class ExamResultResponse(BaseModel):
    exam_id: int
    title: str
    course_code: str
    max_marks: Decimal
    total_marks: Decimal
    percentage: float
    feedback: str | None
    answers: list[ExamResultAnswer]
