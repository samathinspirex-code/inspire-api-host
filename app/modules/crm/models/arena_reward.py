from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ArenaReward(Base):
    __tablename__ = "crm_arena_rewards"

    reward_id: Mapped[int] = mapped_column(primary_key=True)
    reward_type: Mapped[str] = mapped_column(String(20), nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    value_label: Mapped[Optional[str]] = mapped_column(String(80))
    description: Mapped[Optional[str]] = mapped_column(Text)
    tier_index: Mapped[Optional[int]] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ArenaRewardCode(Base):
    __tablename__ = "crm_arena_reward_codes"
    __table_args__ = (UniqueConstraint("reward_id", "code"),)

    code_id: Mapped[int] = mapped_column(primary_key=True)
    reward_id: Mapped[int] = mapped_column(Integer, ForeignKey("crm_arena_rewards.reward_id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(160), nullable=False)
    expires_at: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="available")
    created_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ArenaRewardCollection(Base):
    __tablename__ = "crm_arena_reward_collections"

    collection_id: Mapped[int] = mapped_column(primary_key=True)
    reward_id: Mapped[int] = mapped_column(Integer, ForeignKey("crm_arena_rewards.reward_id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    code_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("crm_arena_reward_codes.code_id"))
    issued_code: Mapped[Optional[str]] = mapped_column(String(160))
    note: Mapped[Optional[str]] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    decided_by: Mapped[Optional[int]] = mapped_column(Integer)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
