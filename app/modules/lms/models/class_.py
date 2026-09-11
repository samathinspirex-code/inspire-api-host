from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LmsClass(Base):
    __tablename__ = "lms_classes"
    __table_args__ = (
        Index("idx_lms_classes_course_id", "course_id"),
        Index("idx_lms_classes_status", "status"),
    )

    class_id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("lms_courses.course_id", ondelete="RESTRICT"), nullable=False
    )
    # The database foreign key is installed by the academic architecture
    # migration. Keep the ORM column mapped so class enrolments can resolve
    # the authoritative CMS Course and its Study Mode.
    academic_course_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivery_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="online")
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="Asia/Colombo")
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="planned")
    study_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
