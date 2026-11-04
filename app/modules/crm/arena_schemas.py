from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

RewardType = Literal["voucher", "code", "gift", "other"]


class ArenaRewardIn(BaseModel):
    reward_type: RewardType
    brand: Optional[str] = Field(None, max_length=120)
    title: str = Field(min_length=1, max_length=160)
    value_label: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = None
    tier_index: Optional[int] = Field(None, ge=0, le=4)
    is_active: bool = True


class ArenaRewardCodesIn(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=1000)
    expires_at: Optional[date] = None


class ArenaSeasonResetIn(BaseModel):
    confirm: str


class ArenaDecisionIn(BaseModel):
    code: Optional[str] = Field(None, max_length=160)
    note: Optional[str] = None


class ArenaAwardIn(ArenaDecisionIn):
    user_id: int
