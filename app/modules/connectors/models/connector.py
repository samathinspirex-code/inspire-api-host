from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Connector(Base):
    __tablename__ = "connectors"

    connector_id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    account_id: Mapped[Optional[str]] = mapped_column(String(160))
    owner: Mapped[Optional[str]] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="awaiting_api")
    created_by: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
