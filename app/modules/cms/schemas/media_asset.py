from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


MEDIA_NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,119}$"


class MediaUploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=120, pattern=MEDIA_NAME_PATTERN)
    content_type: str = Field(..., min_length=3, max_length=120)
    size_bytes: int = Field(..., gt=0, le=52_428_800)
    folder: str = Field("media-library", pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    alt_text: str | None = Field(None, max_length=255)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().lower()


class MediaAssetUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120, pattern=MEDIA_NAME_PATTERN)
    alt_text: str | None = Field(None, max_length=255)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().lower()


class MediaUploadTicket(BaseModel):
    media_asset_id: int
    upload_url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    expires_in_seconds: int


class MediaAssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    media_asset_id: int
    name: str
    original_filename: str
    content_type: str
    size_bytes: int | None
    kind: str
    folder: str
    alt_text: str | None
    public_url: str | None
    status: str
    created_at: datetime


class MediaAssetListResponse(BaseModel):
    data: list[MediaAssetResponse]
