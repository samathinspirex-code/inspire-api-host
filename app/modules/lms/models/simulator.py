from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LmsSimTemplate(Base):
    """A reusable simulator. Starters have a starter_key and no owner."""

    __tablename__ = "lms_sim_templates"

    template_id: Mapped[int] = mapped_column(primary_key=True)
    starter_key: Mapped[str | None] = mapped_column(String(40), unique=True)
    source_template_id: Mapped[int | None] = mapped_column(ForeignKey("lms_sim_templates.template_id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    tagline: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    cover_image_url: Mapped[str | None] = mapped_column(String(1000))
    how_it_works: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    programme: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="ug")
    tone: Mapped[str] = mapped_column(String(20), nullable=False, default="student")
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsSimRun(Base):
    """A template applied to one class. Content is a copy so later template edits never change a live class."""

    __tablename__ = "lms_sim_runs"

    run_id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("lms_classes.class_id", ondelete="CASCADE"), nullable=False)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("lms_sim_templates.template_id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    tagline: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    cover_image_url: Mapped[str | None] = mapped_column(String(1000))
    how_it_works: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tone: Mapped[str] = mapped_column(String(20), nullable=False, default="student")
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    min_team_size: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    max_team_size: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    allow_solo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    xp_per_round: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    points_per_round: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LmsSimTeam(Base):
    __tablename__ = "lms_sim_teams"

    team_id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("lms_sim_runs.run_id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    is_solo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="forming")
    current_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kpi_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    final_score: Mapped[int | None] = mapped_column(Integer)
    final_band: Mapped[str | None] = mapped_column(String(20))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LmsSimTeamMember(Base):
    __tablename__ = "lms_sim_team_members"
    __table_args__ = (
        UniqueConstraint("run_id", "student_user_id", name="uq_lms_sim_member_run"),
        UniqueConstraint("team_id", "role_key", name="uq_lms_sim_member_role"),
    )

    team_id: Mapped[int] = mapped_column(ForeignKey("lms_sim_teams.team_id", ondelete="CASCADE"), primary_key=True)
    student_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("lms_sim_runs.run_id", ondelete="CASCADE"), nullable=False)
    role_key: Mapped[str | None] = mapped_column(String(40))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LmsSimInvite(Base):
    __tablename__ = "lms_sim_invites"

    invite_id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("lms_sim_teams.team_id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[int] = mapped_column(ForeignKey("lms_sim_runs.run_id", ondelete="CASCADE"), nullable=False)
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    student_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LmsSimVote(Base):
    __tablename__ = "lms_sim_votes"

    team_id: Mapped[int] = mapped_column(ForeignKey("lms_sim_teams.team_id", ondelete="CASCADE"), primary_key=True)
    round_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    option_index: Mapped[int] = mapped_column(Integer, nullable=False)
    voted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LmsSimRoundResult(Base):
    __tablename__ = "lms_sim_round_results"

    team_id: Mapped[int] = mapped_column(ForeignKey("lms_sim_teams.team_id", ondelete="CASCADE"), primary_key=True)
    round_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    winners: Mapped[list] = mapped_column(JSON, nullable=False)
    kpi_before: Mapped[dict] = mapped_column(JSON, nullable=False)
    kpi_after: Mapped[dict] = mapped_column(JSON, nullable=False)
    delta: Mapped[dict] = mapped_column(JSON, nullable=False)
    debrief: Mapped[str] = mapped_column(Text, nullable=False, default="")
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LmsSimMessage(Base):
    __tablename__ = "lms_sim_messages"

    message_id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("lms_sim_teams.team_id", ondelete="CASCADE"), nullable=False)
    sender_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LmsXpLedger(Base):
    """Add-only record of XP and points. The leaderboard reads this table."""

    __tablename__ = "lms_xp_ledger"

    entry_id: Mapped[int] = mapped_column(primary_key=True)
    student_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("lms_classes.class_id", ondelete="SET NULL"))
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("lms_sim_runs.run_id", ondelete="SET NULL"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("lms_sim_teams.team_id", ondelete="SET NULL"))
    xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    awarded_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    event_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
