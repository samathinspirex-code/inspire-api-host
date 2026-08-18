import re
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.cms.schemas.common import Pagination

NewsEventStatus = Literal["Draft", "Review", "Published"]
NewsEventKind = Literal["News", "Event"]


class NewsEventCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=255)
    kind: NewsEventKind = "News"
    category: str = Field(..., min_length=1, max_length=100)
    image_url: str = Field(..., min_length=1)
    excerpt: str = Field(..., min_length=1)
    content: list[str] = Field(..., min_length=1)
    author: str = Field(..., min_length=1, max_length=150)
    status: NewsEventStatus = "Draft"
    published_on: Optional[date] = None
    event_date: Optional[date] = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized):
            raise ValueError("must contain lowercase letters, numbers, and single hyphens only")
        return normalized

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: list[str]) -> list[str]:
        paragraphs = [paragraph.strip() for paragraph in value if paragraph.strip()]
        if not paragraphs:
            raise ValueError("must contain at least one paragraph")
        return paragraphs


class NewsEventUpdate(NewsEventCreate):
    pass


class NewsEventItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    news_event_id: int
    slug: str
    title: str
    kind: NewsEventKind
    category: str
    image_url: str
    excerpt: str
    content: list[str]
    author: str
    status: NewsEventStatus
    published_on: Optional[date]
    event_date: Optional[date]
    created_at: datetime
    updated_at: datetime


class NewsEventListResponse(BaseModel):
    data: list[NewsEventItem]
    pagination: Pagination


class PublicNewsEventListResponse(BaseModel):
    data: list[NewsEventItem]
