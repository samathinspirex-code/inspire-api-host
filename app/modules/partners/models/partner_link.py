from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PartnerLink(Base):
    __tablename__ = "partner_links"

    link_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    partner_type: Mapped[str] = mapped_column(String(80), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False, default="all")
    programme: Mapped[Optional[str]] = mapped_column(String(255))
    campaign: Mapped[Optional[str]] = mapped_column(String(160))
    commission: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
