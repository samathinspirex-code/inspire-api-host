from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ArenaClaim(Base):
    __tablename__ = "crm_arena_claims"
    __table_args__ = (UniqueConstraint("user_id", "quest_id", "level", "claim_date", name="ux_crm_arena_claims_level"),)

    claim_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quest_id: Mapped[str] = mapped_column(String(40), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    claim_date: Mapped[date] = mapped_column(Date, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, nullable=False)
    base_xp: Mapped[int | None] = mapped_column(Integer)
    streak_bonus_xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
