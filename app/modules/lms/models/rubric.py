from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LmsRubricTemplate(Base):
    """A reusable ATHE marking rubric shared by every lecturer."""

    __tablename__ = "lms_rubric_templates"
    __table_args__ = (Index("idx_lms_rubric_templates_active", "is_archived", "updated_at"),)

    template_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    form_title: Mapped[str | None] = mapped_column(String(255))
    qualification: Mapped[str | None] = mapped_column(String(255))
    unit_code_title: Mapped[str | None] = mapped_column(String(255))
    default_assignment_title: Mapped[str | None] = mapped_column(String(255))
    source_file_name: Mapped[str | None] = mapped_column(String(255))
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsRubricTemplateCriterion(Base):
    __tablename__ = "lms_rubric_template_criteria"
    __table_args__ = (Index("idx_lms_rubric_template_criteria", "template_id", "position"),)

    criterion_id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("lms_rubric_templates.template_id", ondelete="CASCADE"), nullable=False
    )
    lo_code: Mapped[str] = mapped_column(String(30), nullable=False)
    lo_statement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ac_code: Mapped[str] = mapped_column(String(30), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LmsAssignmentRubric(Base):
    """The rubric attached to one assignment. Criteria are a snapshot so later template edits never change marking."""

    __tablename__ = "lms_assignment_rubrics"

    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("lms_coursework_assignments.assignment_id", ondelete="CASCADE"), primary_key=True
    )
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("lms_rubric_templates.template_id", ondelete="SET NULL")
    )
    template_name: Mapped[str] = mapped_column(String(255), nullable=False)
    form_title: Mapped[str | None] = mapped_column(String(255))
    qualification: Mapped[str | None] = mapped_column(String(255))
    unit_code_title: Mapped[str | None] = mapped_column(String(255))
    assignment_title: Mapped[str | None] = mapped_column(String(255))
    criteria_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    first_assessor_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    second_assessor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    issued_at: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsRubricAssessment(Base):
    """One assessor's rubric form for one student submission."""

    __tablename__ = "lms_rubric_assessments"
    __table_args__ = (
        UniqueConstraint("submission_id", "assessor_role", name="uq_lms_rubric_assessments_submission_role"),
        Index("idx_lms_rubric_assessments_assignment", "assignment_id", "assessor_role"),
    )

    assessment_id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("lms_coursework_assignments.assignment_id", ondelete="CASCADE"), nullable=False
    )
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("lms_coursework_submissions.submission_id", ondelete="CASCADE"), nullable=False
    )
    assessor_role: Mapped[str] = mapped_column(String(10), nullable=False)
    assessor_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    criteria_results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    actions_needed: Mapped[str | None] = mapped_column(Text)
    overall_comment: Mapped[str | None] = mapped_column(Text)
    resubmission: Mapped[str | None] = mapped_column(String(100))
    assessment_date: Mapped[date | None] = mapped_column(Date)
    overall_grade: Mapped[str | None] = mapped_column(String(30))
    grade_summary: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
