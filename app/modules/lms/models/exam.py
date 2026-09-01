from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LmsExam(Base):
    __tablename__ = "lms_exams"
    __table_args__ = (Index("idx_lms_exams_course_status", "course_id", "status"),)

    exam_id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("lms_coursework_assignments.assignment_id", ondelete="CASCADE"), unique=True, nullable=False
    )
    course_id: Mapped[int] = mapped_column(ForeignKey("lms_courses.course_id", ondelete="CASCADE"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, default="course")
    target_id: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    randomize_questions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    randomize_options: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    grades_released: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsExamQuestion(Base):
    __tablename__ = "lms_exam_questions"
    __table_args__ = (Index("idx_lms_exam_questions_exam", "exam_id", "position"),)

    question_id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("lms_exams.exam_id", ondelete="CASCADE"), nullable=False)
    question_type: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    marks: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    options: Mapped[list | None] = mapped_column(JSON)
    correct_option_index: Mapped[int | None] = mapped_column(Integer)
    accepted_answers: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsExamAttempt(Base):
    __tablename__ = "lms_exam_attempts"
    __table_args__ = (
        UniqueConstraint("exam_id", "student_user_id", name="uq_lms_exam_attempt_student"),
        Index("idx_lms_exam_attempts_exam", "exam_id", "status"),
    )

    attempt_id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("lms_exams.exam_id", ondelete="CASCADE"), nullable=False)
    student_user_id: Mapped[int] = mapped_column(ForeignKey("lms_student_profiles.user_id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="in_progress")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    question_order: Mapped[list] = mapped_column(JSON, nullable=False)
    option_orders: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    auto_marks: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=0)
    manual_marks: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    total_marks: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    feedback: Mapped[str | None] = mapped_column(Text)
    marked_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsExamAnswer(Base):
    __tablename__ = "lms_exam_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_lms_exam_answer_question"),)

    answer_id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("lms_exam_attempts.attempt_id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[int] = mapped_column(ForeignKey("lms_exam_questions.question_id", ondelete="CASCADE"), nullable=False)
    answer_text: Mapped[str | None] = mapped_column(Text)
    selected_option_index: Mapped[int | None] = mapped_column(Integer)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    auto_marks: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=0)
    manual_marks: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    feedback: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
