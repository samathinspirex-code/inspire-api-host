from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CourseLecturer(Base):
    __tablename__ = "lms_course_lecturers"
    __table_args__ = (Index("idx_course_lecturers_lecturer", "lecturer_user_id"),)

    course_id: Mapped[int] = mapped_column(
        ForeignKey("lms_courses.course_id", ondelete="CASCADE"), primary_key=True
    )
    lecturer_user_id: Mapped[int] = mapped_column(
        ForeignKey("lms_lecturer_profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClassLecturer(Base):
    __tablename__ = "lms_class_lecturers"
    __table_args__ = (Index("idx_class_lecturers_lecturer", "lecturer_user_id"),)

    class_id: Mapped[int] = mapped_column(
        ForeignKey("lms_classes.class_id", ondelete="CASCADE"), primary_key=True
    )
    lecturer_user_id: Mapped[int] = mapped_column(
        ForeignKey("lms_lecturer_profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CourseEnrollment(Base):
    __tablename__ = "lms_course_enrollments"
    __table_args__ = (Index("idx_course_enrollments_student", "student_user_id"),)

    course_id: Mapped[int] = mapped_column(
        ForeignKey("lms_courses.course_id", ondelete="CASCADE"), primary_key=True
    )
    student_user_id: Mapped[int] = mapped_column(
        ForeignKey("lms_student_profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="enrolled")
    enrolled_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ClassStudent(Base):
    __tablename__ = "lms_class_students"
    __table_args__ = (Index("idx_class_students_student", "student_user_id"),)

    class_id: Mapped[int] = mapped_column(
        ForeignKey("lms_classes.class_id", ondelete="CASCADE"), primary_key=True
    )
    student_user_id: Mapped[int] = mapped_column(
        ForeignKey("lms_student_profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
