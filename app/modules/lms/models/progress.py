from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LmsLearningProgress(Base):
    __tablename__ = "lms_learning_progress"
    __table_args__ = (
        UniqueConstraint("learning_item_id", "student_user_id", name="uq_lms_learning_progress_item_student"),
        Index("idx_lms_learning_progress_student", "student_user_id", "last_activity_at"),
        Index("idx_lms_learning_progress_item", "learning_item_id"),
        CheckConstraint("watched_seconds >= 0", name="ck_lms_learning_progress_watched_nonnegative"),
        CheckConstraint("last_position_seconds >= 0", name="ck_lms_learning_progress_position_nonnegative"),
        CheckConstraint("completion_percent >= 0 AND completion_percent <= 100", name="ck_lms_learning_progress_percent"),
    )

    progress_id: Mapped[int] = mapped_column(primary_key=True)
    learning_item_id: Mapped[int] = mapped_column(
        ForeignKey("lms_learning_items.learning_item_id", ondelete="CASCADE"), nullable=False
    )
    student_user_id: Mapped[int] = mapped_column(
        ForeignKey("lms_student_profiles.user_id", ondelete="CASCADE"), nullable=False
    )
    watched_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_position_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
