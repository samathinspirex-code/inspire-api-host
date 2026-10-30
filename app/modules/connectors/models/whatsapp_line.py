from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WhatsAppLine(Base):
    __tablename__ = "whatsapp_lines"

    line_id: Mapped[int] = mapped_column(primary_key=True)
    counsellor_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    phone_number: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(160))
    phone_number_id: Mapped[Optional[str]] = mapped_column(String(80))
    created_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
