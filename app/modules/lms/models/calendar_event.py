from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LmsCalendarEvent(Base):
    __tablename__ = "lms_calendar_events"
    __table_args__ = (
        CheckConstraint(
            "audience_type IN ('university', 'programme', 'class')",
            name="ck_lms_calendar_events_audience_type",
        ),
        CheckConstraint("status IN ('scheduled', 'cancelled')", name="ck_lms_calendar_events_status"),
        CheckConstraint("end_time > start_time", name="ck_lms_calendar_events_time"),
        Index("idx_lms_calendar_events_start", "start_time"),
        Index("idx_lms_calendar_events_audience", "audience_type", "program_id", "class_id"),
    )

    event_id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    audience_type: Mapped[str] = mapped_column(String(20), nullable=False)
    program_id: Mapped[int | None] = mapped_column(ForeignKey("programs.program_id", ondelete="CASCADE"))
    class_id: Mapped[int | None] = mapped_column(ForeignKey("lms_classes.class_id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
