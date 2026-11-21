from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AnnouncementCreate(BaseModel):
    audience_type: Literal["all", "admin", "super_admin", "course", "class", "classes", "part_time", "full_time"]
    audience_id: int | None = Field(None, gt=0)
    audience_class_ids: list[int] = Field(default_factory=list, max_length=200)
    title: str = Field(..., min_length=2, max_length=255)
    message: str = Field(..., min_length=2, max_length=20_000)
    importance: Literal["normal", "important", "urgent"] = "normal"
    publish_at: datetime
    expires_at: datetime | None = None
    status: Literal["draft", "scheduled", "published"] = "scheduled"
    email_enabled: bool = True
    attachment_asset_ids: list[int] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_announcement(self):
        if len(self.attachment_asset_ids) != len(set(self.attachment_asset_ids)) or any(asset_id <= 0 for asset_id in self.attachment_asset_ids):
            raise ValueError("Choose up to ten distinct uploaded files")
        if len(self.audience_class_ids) != len(set(self.audience_class_ids)) or any(class_id <= 0 for class_id in self.audience_class_ids):
            raise ValueError("Choose each class only once")
        if self.audience_type == "classes":
            if len(self.audience_class_ids) < 2:
                raise ValueError("Choose at least two classes, or use the Class audience for one")
            if self.audience_id is not None:
                raise ValueError("Several classes are chosen with audience_class_ids")
            return self._validate_dates()
        if self.audience_class_ids:
            raise ValueError("Only the Several classes audience uses a class list")
        if self.audience_type in {"course", "class"} and self.audience_id is None:
            raise ValueError("Choose a course or class audience")
        if self.audience_type not in {"course", "class"} and self.audience_id is not None:
            raise ValueError("This audience does not use a course or class ID")
        return self._validate_dates()

    def _validate_dates(self):
        if self.expires_at and self.expires_at <= self.publish_at:
            raise ValueError("Expiry must be after publication")
        return self


class AnnouncementAttachment(BaseModel):
    media_asset_id: int
    name: str
    content_type: str
    size_bytes: int | None
    url: str


class AnnouncementItem(BaseModel):
    announcement_id: int
    audience_type: str
    audience_id: int | None
    audience_class_ids: list[int] = Field(default_factory=list)
    audience_label: str
    title: str
    message: str
    attachments: list[AnnouncementAttachment] = Field(default_factory=list)
    importance: str
    publish_at: datetime
    expires_at: datetime | None
    status: str
    email_enabled: bool
    created_by: int
    created_at: datetime


class AnnouncementListResponse(BaseModel):
    data: list[AnnouncementItem]


class AnnouncementStatusUpdate(BaseModel):
    status: Literal["cancelled", "scheduled", "published"]


class NotificationItem(BaseModel):
    notification_id: int
    announcement_id: int | None = None
    notification_type: str
    title: str
    message: str
    action_url: str | None
    importance: str
    scheduled_for: datetime
    read_at: datetime | None
    email_status: str
    created_at: datetime


class NotificationListResponse(BaseModel):
    unread_count: int
    data: list[NotificationItem]


class NotificationReadUpdate(BaseModel):
    read: bool = True


class NotificationDispatchSummary(BaseModel):
    reminders_created: int
    announcements_published: int
    emails_sent: int
    emails_failed: int


class NotificationPreferenceItem(BaseModel):
    category: str
    label: str
    description: str
    in_app_enabled: bool
    email_enabled: bool


class NotificationPreferencesResponse(BaseModel):
    data: list[NotificationPreferenceItem]


class NotificationPreferenceChange(BaseModel):
    category: str = Field(..., min_length=2, max_length=40)
    in_app_enabled: bool
    email_enabled: bool


class NotificationPreferencesUpdate(BaseModel):
    data: list[NotificationPreferenceChange] = Field(..., min_length=1, max_length=20)
