from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LmsLearningItem(Base):
    __tablename__ = "lms_learning_items"
    __table_args__ = (
        UniqueConstraint("module_id", "position", name="uq_lms_learning_items_module_position"),
        Index("idx_lms_learning_items_module", "module_id"),
    )

    learning_item_id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(
        ForeignKey("lms_modules.module_id", ondelete="CASCADE"), nullable=False
    )
    item_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LmsModuleAccess(Base):
    __tablename__ = "lms_module_access"
    __table_args__ = (
        UniqueConstraint("module_id", "scope_type", "scope_id", name="uq_lms_module_access_scope"),
        Index("idx_lms_module_access_lookup", "module_id", "scope_type", "scope_id"),
    )

    module_access_id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(
        ForeignKey("lms_modules.module_id", ondelete="CASCADE"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_unlocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LmsCourseDiscussion(Base):
    __tablename__ = "lms_course_discussions"
    __table_args__ = (Index("idx_lms_course_discussions_course_created", "course_id", "created_at"),)

    discussion_id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("lms_courses.course_id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LmsCourseAssistantSettings(Base):
    __tablename__ = "lms_course_assistant_settings"

    course_id: Mapped[int] = mapped_column(
        ForeignKey("lms_courses.course_id", ondelete="CASCADE"), primary_key=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assistant_name: Mapped[str] = mapped_column(String(80), nullable=False, default="Course Assistant")
    welcome_message: Mapped[str] = mapped_column(
        Text, nullable=False, default="Hi! What would you like to understand about this course?"
    )
    fallback_message: Mapped[str] = mapped_column(
        Text, nullable=False, default="I couldn't find that in the approved course resources yet."
    )
    attention_animation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LmsCourseAssistantSystemSettings(Base):
    __tablename__ = "lms_course_assistant_system_settings"

    settings_id: Mapped[int] = mapped_column(primary_key=True, default=1)
    automation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_generate_questions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    questions_per_video: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LmsCourseKnowledgeSource(Base):
    __tablename__ = "lms_course_knowledge_sources"
    __table_args__ = (
        Index("idx_lms_course_knowledge_sources_course", "course_id"),
        Index("idx_lms_course_knowledge_sources_item", "learning_item_id"),
        UniqueConstraint("course_id", "sync_key", name="uq_lms_course_knowledge_sources_sync_key"),
    )

    knowledge_source_id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("lms_courses.course_id", ondelete="CASCADE"), nullable=False
    )
    learning_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("lms_learning_items.learning_item_id", ondelete="CASCADE"), nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sync_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ingestion_status: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LmsCourseKnowledgeChunk(Base):
    __tablename__ = "lms_course_knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("knowledge_source_id", "position", name="uq_lms_course_knowledge_chunks_position"),
        Index("idx_lms_course_knowledge_chunks_source", "knowledge_source_id"),
    )

    knowledge_chunk_id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_source_id: Mapped[int] = mapped_column(
        ForeignKey("lms_course_knowledge_sources.knowledge_source_id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LmsLectureQuestion(Base):
    __tablename__ = "lms_lecture_questions"
    __table_args__ = (
        Index("idx_lms_lecture_questions_item_status", "learning_item_id", "status"),
        Index("idx_lms_lecture_questions_course", "course_id"),
    )

    question_id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("lms_courses.course_id", ondelete="CASCADE"), nullable=False
    )
    learning_item_id: Mapped[int] = mapped_column(
        ForeignKey("lms_learning_items.learning_item_id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    option_a: Mapped[str] = mapped_column(Text, nullable=False)
    option_b: Mapped[str] = mapped_column(Text, nullable=False)
    option_c: Mapped[str] = mapped_column(Text, nullable=False)
    option_d: Mapped[str] = mapped_column(Text, nullable=False)
    correct_option: Mapped[str] = mapped_column(String(1), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    topic: Mapped[str] = mapped_column(String(120), nullable=False, default="General")
    source_locator: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="generated")
    generated_by_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LmsLectureQuizAttempt(Base):
    __tablename__ = "lms_lecture_quiz_attempts"
    __table_args__ = (
        Index("idx_lms_lecture_quiz_attempts_student_item", "student_user_id", "learning_item_id"),
    )

    attempt_id: Mapped[int] = mapped_column(primary_key=True)
    learning_item_id: Mapped[int] = mapped_column(
        ForeignKey("lms_learning_items.learning_item_id", ondelete="CASCADE"), nullable=False
    )
    student_user_id: Mapped[int] = mapped_column(
        ForeignKey("lms_student_profiles.user_id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LmsLectureQuizAttemptQuestion(Base):
    __tablename__ = "lms_lecture_quiz_attempt_questions"
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_id", name="uq_lms_attempt_question"),
        UniqueConstraint("attempt_id", "position", name="uq_lms_attempt_question_position"),
    )

    attempt_question_id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("lms_lecture_quiz_attempts.attempt_id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("lms_lecture_questions.question_id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_option: Mapped[str | None] = mapped_column(String(1), nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
