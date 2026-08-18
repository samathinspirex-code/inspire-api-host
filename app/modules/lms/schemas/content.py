from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LearningItemType = Literal["video", "pdf", "text", "link", "assignment", "quiz"]
LearningItemStatus = Literal["draft", "published"]
AccessScope = Literal["course", "class", "student"]


class LearningItemCreate(BaseModel):
    item_type: LearningItemType
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    resource_url: str | None = Field(None, max_length=5000)
    text_content: str | None = Field(None, max_length=30000)
    duration_minutes: int | None = Field(None, ge=1, le=10000)
    status: LearningItemStatus = "draft"
    is_required: bool = True

    @model_validator(mode="after")
    def validate_resource(self):
        if self.item_type in {"video", "pdf", "link"} and not self.resource_url:
            raise ValueError(f"resource_url is required for {self.item_type} content")
        if self.item_type == "text" and not self.text_content:
            raise ValueError("text_content is required for text content")
        return self


class LearningItemUpdate(LearningItemCreate):
    pass


class LearningItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    learning_item_id: int
    module_id: int
    item_type: LearningItemType
    title: str
    description: str | None
    resource_url: str | None
    text_content: str | None
    duration_minutes: int | None
    position: int
    status: LearningItemStatus
    is_required: bool
    created_at: datetime
    updated_at: datetime
    progress_percent: float = 0
    is_completed: bool = False
    watched_seconds: int = 0
    duration_seconds: int | None = None
    last_position_seconds: int = 0
    download_allowed: bool = True


class LearningItemReorderRequest(BaseModel):
    learning_item_ids: list[int] = Field(..., min_length=1)


class ModuleAccessUpdate(BaseModel):
    scope_type: AccessScope
    scope_id: int = Field(..., gt=0)
    is_unlocked: bool = True
    available_from: datetime | None = None


class ModuleAccessResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    module_access_id: int
    module_id: int
    scope_type: AccessScope
    scope_id: int
    is_unlocked: bool
    available_from: datetime | None


class StudioSectionResponse(BaseModel):
    module_id: int
    course_id: int
    title: str
    description: str | None
    position: int
    status: Literal["draft", "active"]
    is_unlocked: bool
    locked_reason: str | None
    items: list[LearningItemResponse]
    access_rules: list[ModuleAccessResponse] = Field(default_factory=list)


class CourseStudioResponse(BaseModel):
    course_id: int
    can_manage: bool
    sections: list[StudioSectionResponse]


class CourseDiscussionCreate(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)


class CourseDiscussionItem(BaseModel):
    discussion_id: int
    course_id: int
    author_user_id: int
    author_name: str
    author_role: Literal["LECTURER", "STUDENT"]
    message: str
    created_at: datetime


class CourseDiscussionListResponse(BaseModel):
    data: list[CourseDiscussionItem]


KnowledgeSourceType = Literal["video_transcript", "pdf", "text_lesson", "lecturer_note", "faq"]


class CourseAssistantSettingsUpdate(BaseModel):
    is_enabled: bool = False
    assistant_name: str = Field("Course Assistant", min_length=1, max_length=80)
    welcome_message: str = Field(..., min_length=1, max_length=1000)
    fallback_message: str = Field(..., min_length=1, max_length=1000)
    attention_animation: bool = True


class CourseAssistantSettingsResponse(CourseAssistantSettingsUpdate):
    course_id: int


class CourseKnowledgeSourceCreate(BaseModel):
    learning_item_id: int | None = Field(None, gt=0)
    source_type: KnowledgeSourceType
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=50000)
    source_url: str | None = Field(None, max_length=5000)
    page_number: int | None = Field(None, ge=1)
    start_seconds: int | None = Field(None, ge=0)
    end_seconds: int | None = Field(None, ge=0)
    is_approved: bool = True

    @model_validator(mode="after")
    def validate_locator(self):
        if self.end_seconds is not None and self.start_seconds is None:
            raise ValueError("start_seconds is required when end_seconds is provided")
        if self.end_seconds is not None and self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds cannot be earlier than start_seconds")
        return self


class CourseKnowledgeSourceUpdate(CourseKnowledgeSourceCreate):
    pass


class CourseKnowledgeSourceResponse(CourseKnowledgeSourceCreate):
    knowledge_source_id: int
    course_id: int
    ingestion_status: str = "manual"
    chunk_count: int = 0
    indexed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class CourseAssistantAdminResponse(BaseModel):
    course_id: int
    course_code: str
    course_title: str
    program_title: str
    settings: CourseAssistantSettingsResponse
    sources: list[CourseKnowledgeSourceResponse] = Field(default_factory=list)


class CourseAssistantCatalogItem(BaseModel):
    course_id: int
    course_code: str
    course_title: str
    program_title: str
    is_enabled: bool
    source_count: int


class CourseAssistantCatalogResponse(BaseModel):
    data: list[CourseAssistantCatalogItem]


class CourseAssistantPublicResponse(BaseModel):
    course_id: int
    is_enabled: bool
    assistant_name: str
    welcome_message: str
    attention_animation: bool
    suggested_questions: list[str] = Field(default_factory=list)


class CourseAssistantQuestion(BaseModel):
    question: str = Field(..., min_length=2, max_length=1000)


class CourseAssistantCitation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    knowledge_source_id: int
    title: str
    source_type: KnowledgeSourceType
    source_url: str | None
    page_number: int | None
    start_seconds: int | None
    end_seconds: int | None


class CourseAssistantAnswer(BaseModel):
    answer: str
    grounded: bool
    citations: list[CourseAssistantCitation] = Field(default_factory=list)


class CourseAssistantIngestionFailure(BaseModel):
    learning_item_id: int
    title: str
    reason: str


class CourseAssistantIngestionResponse(BaseModel):
    course_id: int
    items_scanned: int
    sources_indexed: int
    chunks_created: int
    failures: list[CourseAssistantIngestionFailure] = Field(default_factory=list)
