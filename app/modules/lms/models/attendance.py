from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AttendanceSession(Base):
    __tablename__ = "lms_attendance_sessions"
    __table_args__ = (
        CheckConstraint("threshold_percentage BETWEEN 1 AND 100", name="ck_lms_attendance_threshold"),
        CheckConstraint("sync_status IN ('pending', 'synced', 'failed')", name="ck_lms_attendance_sync_status"),
        Index("idx_lms_attendance_sessions_class", "class_id"),
        Index("idx_lms_attendance_sessions_status", "sync_status"),
    )

    attendance_session_id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("lms_online_meetings.meeting_id", ondelete="CASCADE"), nullable=False, unique=True
    )
    class_id: Mapped[int] = mapped_column(
        ForeignKey("lms_classes.class_id", ondelete="CASCADE"), nullable=False
    )
    google_conference_record_name: Mapped[str | None] = mapped_column(String(255))
    actual_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    threshold_percentage: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=50)
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    sync_error: Mapped[str | None] = mapped_column(Text)
    unmatched_participants: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    synced_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AttendanceRecord(Base):
    __tablename__ = "lms_attendance_records"
    __table_args__ = (
        CheckConstraint("status IN ('present', 'absent')", name="ck_lms_attendance_record_status"),
        CheckConstraint("attended_seconds >= 0", name="ck_lms_attendance_duration"),
        CheckConstraint("attendance_percentage BETWEEN 0 AND 100", name="ck_lms_attendance_percentage"),
        CheckConstraint("source IN ('google_meet', 'manual_override')", name="ck_lms_attendance_source"),
        UniqueConstraint("attendance_session_id", "student_user_id", name="uq_lms_attendance_record_student"),
        Index("idx_lms_attendance_records_student", "student_user_id"),
        Index("idx_lms_attendance_records_status", "status"),
    )

    attendance_record_id: Mapped[int] = mapped_column(primary_key=True)
    attendance_session_id: Mapped[int] = mapped_column(
        ForeignKey("lms_attendance_sessions.attendance_session_id", ondelete="CASCADE"), nullable=False
    )
    student_user_id: Mapped[int] = mapped_column(
        ForeignKey("lms_student_profiles.user_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    attended_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attendance_percentage: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    first_join_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_leave_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    google_participant_name: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="google_meet")
    overridden_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    override_reason: Mapped[str | None] = mapped_column(Text)
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
