from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OnlineMeeting(Base):
    __tablename__ = "lms_online_meetings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'cancelled', 'completed')",
            name="ck_lms_online_meetings_status",
        ),
        CheckConstraint(
            "provider IN ('google', 'zoom')",
            name="ck_lms_online_meetings_provider",
        ),
        CheckConstraint(
            "calendar_sync_status IN ('synced', 'disabled', 'failed')",
            name="ck_lms_online_meetings_calendar_sync",
        ),
        Index("idx_lms_online_meetings_class", "class_id"),
        Index("idx_lms_online_meetings_lecturer", "lecturer_user_id"),
        Index("idx_lms_online_meetings_start", "start_time"),
    )

    meeting_id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("lms_classes.class_id", ondelete="CASCADE"), nullable=False
    )
    audience_type: Mapped[str] = mapped_column(String(20), nullable=False, default="class")
    audience_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lecturer_user_id: Mapped[int] = mapped_column(
        ForeignKey("lms_lecturer_profiles.user_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="google")
    join_uri: Mapped[str] = mapped_column(Text, nullable=False, default="")
    provider_meeting_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_meeting_uuid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    zoom_host_connection_id: Mapped[int | None] = mapped_column(nullable=True)
    zoom_passcode_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_applicable")
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_space_name: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    google_meeting_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_meeting_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    google_calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_calendar_event_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    calendar_sync_status: Mapped[str] = mapped_column(String(20), nullable=False)
    calendar_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    students_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
