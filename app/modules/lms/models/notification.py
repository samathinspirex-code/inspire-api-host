from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LmsAnnouncement(Base):
    __tablename__ = "lms_announcements"
    __table_args__ = (Index("idx_lms_announcements_publish", "status", "publish_at"),)

    announcement_id: Mapped[int] = mapped_column(primary_key=True)
    audience_type: Mapped[str] = mapped_column(String(20), nullable=False)
    audience_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsNotification(Base):
    __tablename__ = "lms_notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "event_key", name="uq_lms_notification_user_event"),
        Index("idx_lms_notifications_user", "user_id", "created_at"),
        Index("idx_lms_notifications_delivery", "email_status", "scheduled_for"),
    )

    notification_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    action_url: Mapped[str | None] = mapped_column(Text)
    importance: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    email_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_provider_id: Mapped[str | None] = mapped_column(String(255))
    email_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

