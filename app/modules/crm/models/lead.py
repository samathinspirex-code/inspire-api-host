from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, func
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
    awarding_body: Mapped[Optional[str]] = mapped_column(String(100))
    message: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    stage: Mapped[str] = mapped_column(String(50), nullable=False, default="new_lead")
    status_reason: Mapped[Optional[str]] = mapped_column(String(150))
    status_remarks: Mapped[Optional[str]] = mapped_column(Text)
    affordability_reason: Mapped[Optional[str]] = mapped_column(String(150))
    expected_intake: Mapped[Optional[str]] = mapped_column(String(100))
    expected_month: Mapped[Optional[str]] = mapped_column(String(20))
    delay_reason: Mapped[Optional[str]] = mapped_column(String(150))
    campaign: Mapped[Optional[str]] = mapped_column(String(255))
    academic_course_id: Mapped[Optional[int]] = mapped_column(Integer)
    programme_fee: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    registration_fee: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    programme_duration: Mapped[Optional[str]] = mapped_column(String(100))
    intake: Mapped[Optional[str]] = mapped_column(String(100))
    payment_plan: Mapped[Optional[str]] = mapped_column(Text)
    last_contacted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    followup_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enrolled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    student_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    student_id: Mapped[Optional[str]] = mapped_column(String(100))
    payment_status: Mapped[Optional[str]] = mapped_column(String(30))
    amount_paid: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    payment_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    enrollment_email_status: Mapped[Optional[str]] = mapped_column(String(30))
    enrollment_email_error: Mapped[Optional[str]] = mapped_column(Text)
    enrollment_email_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    assigned_counsellor_id: Mapped[Optional[int]] = mapped_column(Integer)
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
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
    external_record_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    activities: Mapped[list["CrmActivity"]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        order_by="CrmActivity.created_at.desc()",
    )
