from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LmsCourse(Base):
    __tablename__ = "lms_courses"
    __table_args__ = (
        Index("idx_lms_courses_program_id", "program_id"),
        Index("idx_lms_courses_status", "status"),
    )

    course_id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int | None] = mapped_column(ForeignKey("programs.program_id", ondelete="RESTRICT"), nullable=True)
    is_orientation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # The academic catalogue is managed by SQL migrations rather than ORM
    # models. Its database foreign key is installed by the catalogue-link
    # migration, while this plain mapped column keeps isolated LMS metadata
    # (including the SQLite unit-test schema) independently creatable.
    catalogue_course_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    takeaways: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    vimeo_folder_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_class_copy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_master_course_id: Mapped[int | None] = mapped_column(
        ForeignKey("lms_courses.course_id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
