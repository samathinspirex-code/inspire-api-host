from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PartnerClick(Base):
    __tablename__ = "partner_link_clicks"

    click_id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    platform: Mapped[Optional[str]] = mapped_column(String(40))
    utm_source: Mapped[Optional[str]] = mapped_column(String(120))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(120))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(160))
    ip_hash: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
