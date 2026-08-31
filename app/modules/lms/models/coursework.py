from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LmsCourseworkAssignment(Base):
    __tablename__ = "lms_coursework_assignments"
    __table_args__ = (
        Index("idx_lms_coursework_course_status", "course_id", "status"),
        Index("idx_lms_coursework_target", "target_type", "target_id"),
    )

    assignment_id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("lms_courses.course_id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, default="course")
    target_id: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    assignment_type: Mapped[str] = mapped_column(String(20), nullable=False, default="regular")
    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    max_marks: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=100)
    allow_late: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    grades_released: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LmsCourseworkSubmission(Base):
    __tablename__ = "lms_coursework_submissions"
    __table_args__ = (
        UniqueConstraint("assignment_id", "student_user_id", name="uq_lms_coursework_submission_student"),
        Index("idx_lms_coursework_submission_assignment", "assignment_id", "status"),
        Index("idx_lms_coursework_submission_student", "student_user_id", "updated_at"),
    )

    submission_id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("lms_coursework_assignments.assignment_id", ondelete="CASCADE"), nullable=False
    )
    student_user_id: Mapped[int] = mapped_column(
        ForeignKey("lms_student_profiles.user_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="in_progress")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answer_text: Mapped[str | None] = mapped_column(Text)
    attachment_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_assets.media_asset_id", ondelete="SET NULL")
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    marks_awarded: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    feedback: Mapped[str | None] = mapped_column(Text)
    marked_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
