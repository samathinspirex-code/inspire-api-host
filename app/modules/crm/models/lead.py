from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.modules.crm.models.activity import CrmActivity


class CrmLead(Base):
    __tablename__ = "crm_leads"

    lead_id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    whatsapp: Mapped[Optional[str]] = mapped_column(String(50))
    city: Mapped[Optional[str]] = mapped_column(String(100))
    district: Mapped[Optional[str]] = mapped_column(String(100))
    location_lat: Mapped[Optional[float]] = mapped_column(Float)
    location_lng: Mapped[Optional[float]] = mapped_column(Float)
    highest_qualification: Mapped[Optional[str]] = mapped_column(String(100))
    interested_programme: Mapped[Optional[str]] = mapped_column(String(255))
    interested_course: Mapped[Optional[str]] = mapped_column(String(255))
    message: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    stage: Mapped[str] = mapped_column(String(50), nullable=False, default="new_inquiry")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    assigned_counsellor_id: Mapped[Optional[int]] = mapped_column(Integer)
    counsellor_name: Mapped[Optional[str]] = mapped_column(String(100))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    followup_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    followup_type: Mapped[Optional[str]] = mapped_column(String(50))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Extended fields from Zoho / Excel CRM template
    amount: Mapped[Optional[float]] = mapped_column(Float)
    school: Mapped[Optional[str]] = mapped_column(String(255))
    nationality: Mapped[Optional[str]] = mapped_column(String(100))
    faculty: Mapped[Optional[str]] = mapped_column(String(100))
    student_status: Mapped[Optional[str]] = mapped_column(String(100))
    parents_occupation: Mapped[Optional[str]] = mapped_column(String(255))
    parents_email: Mapped[Optional[str]] = mapped_column(String(255))
    address_line1: Mapped[Optional[str]] = mapped_column(String(255))
    address_line2: Mapped[Optional[str]] = mapped_column(String(255))
    country: Mapped[Optional[str]] = mapped_column(String(100))
    social_lead_id: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    activities: Mapped[list["CrmActivity"]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        order_by="CrmActivity.created_at.desc()",
    )
