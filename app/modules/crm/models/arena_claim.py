from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ArenaClaim(Base):
    __tablename__ = "crm_arena_claims"
    __table_args__ = (UniqueConstraint("user_id", "quest_id", "claim_date"),)

    claim_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quest_id: Mapped[str] = mapped_column(String(40), nullable=False)
    claim_date: Mapped[date] = mapped_column(Date, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
