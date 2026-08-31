from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.lms.schemas.content import LearningItemType


class LearningProgressUpdate(BaseModel):
    position_seconds: int = Field(0, ge=0, le=86400)
    duration_seconds: int | None = Field(None, ge=1, le=86400)
    watched_seconds_delta: int = Field(0, ge=0, le=60)
    event: Literal["heartbeat", "pause", "ended", "complete"] = "heartbeat"


class LearningProgressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    progress_id: int
    learning_item_id: int
    student_user_id: int
    watched_seconds: int
    duration_seconds: int | None
    last_position_seconds: int
    completion_percent: float
    is_completed: bool
    completed_at: datetime | None
    last_activity_at: datetime


class ProgressLearningItem(BaseModel):
    learning_item_id: int
    title: str
    item_type: LearningItemType
    position: int
    is_required: bool
    progress: LearningProgressResponse | None = None
    quiz_attempt_count: int = 0
    quiz_first_attempt_percent: float | None = None
    quiz_best_attempt_percent: float | None = None


class ProgressSection(BaseModel):
    module_id: int
    title: str
    position: int
    total_items: int
    completed_items: int
    completion_percent: float
    items: list[ProgressLearningItem]


class StudentCourseProgressResponse(BaseModel):
    course_id: int
    student_user_id: int
    total_items: int
    completed_items: int
    completion_percent: float
    last_activity_at: datetime | None
    sections: list[ProgressSection]


class StudentProgressSummary(BaseModel):
    student_user_id: int
    completion_percent: float


class CourseProgressSummaryResponse(BaseModel):
    course_id: int
    data: list[StudentProgressSummary]
