import re
from datetime import date, datetime
from typing import Literal, Optional

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.cms.schemas.common import Pagination

NewsEventStatus = Literal["Draft", "Review", "Published"]
NewsEventKind = Literal["News", "Event"]
VideoOrientation = Literal["portrait", "landscape"]


def _clean_media_reference(value: Optional[str]) -> Optional[str]:
    """Accept an HTTPS URL or a media-library asset name; blank means no media."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if "://" in value or value.startswith("//"):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use an HTTPS media URL or a media library name")
    return value


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
    video_url: Optional[str] = None
    video_orientation: Optional[VideoOrientation] = None
    video_poster_url: Optional[str] = None

    @field_validator("video_url", "video_poster_url", mode="before")
    @classmethod
    def validate_video_media(cls, value):
        return _clean_media_reference(value)

    @model_validator(mode="after")
    def match_video_fields(self):
        if self.video_url:
            if self.video_orientation is None:
                self.video_orientation = "landscape"
        else:
            self.video_orientation = None
            self.video_poster_url = None
        return self

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

    @field_validator("image_url")
    @classmethod
    def use_bundled_legacy_image(cls, value: str) -> str:
        legacy_prefix = "https://inspirecollege.lk/wp-content/uploads/"
        if value.startswith(legacy_prefix):
            return f"https://inspirecollege.lk/uploads/{value[len(legacy_prefix):]}"
        return value

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
    video_url: Optional[str] = None
    video_orientation: Optional[VideoOrientation] = None
    video_poster_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class NewsEventListResponse(BaseModel):
    data: list[NewsEventItem]
    pagination: Pagination


class PublicNewsEventListResponse(BaseModel):
    data: list[NewsEventItem]
