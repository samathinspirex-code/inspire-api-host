from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LmsAssessmentTemplate(Base):
    __tablename__ = "lms_assessment_templates"
    __table_args__ = (Index("idx_lms_assessment_templates_kind", "kind", "updated_at"),)

    template_id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    assignment_type: Mapped[str | None] = mapped_column(String(20))
    submission_type: Mapped[str | None] = mapped_column(String(30))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    max_marks: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsAssessmentTemplateQuestion(Base):
    __tablename__ = "lms_assessment_template_questions"
    __table_args__ = (Index("idx_lms_assessment_template_questions", "template_id", "position"),)

    question_id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("lms_assessment_templates.template_id", ondelete="CASCADE"), nullable=False
    )
    question_type: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    marks: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    options: Mapped[list | None] = mapped_column(JSON)
    correct_option_index: Mapped[int | None] = mapped_column(Integer)
    correct_option_indices: Mapped[list | None] = mapped_column(JSON)
    accepted_answers: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
