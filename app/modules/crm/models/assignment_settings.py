from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CrmAssignmentSettings(Base):
    __tablename__ = "crm_assignment_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    auto_assign_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_counsellor_id: Mapped[Optional[int]] = mapped_column(Integer)
    last_assigned_on: Mapped[Optional[date]] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
