from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.crm import service
from app.modules.crm.schemas import CrmLeadCreate

router = APIRouter(prefix="/api/v1/crm/public", tags=["crm-public-intake"])


def extract_client_location(
    request: Request,
    user_city: Optional[str] = None,
    user_district: Optional[str] = None,
    user_lat: Optional[float] = None,
    user_lng: Optional[float] = None,
):
    """
    Secondary Location Approach:
    Server-side IP & Reverse-Proxy Geolocation detection.
    Transparent & 0% friction (no browser prompt):
    1. If user typed/selected city or district, honor it.
    2. Else detect city from Cloudflare header (CF-IPCity) or X-Client-City.
    3. Detect country from CF-IPCountry or X-Country-Code (default: 'Sri Lanka').
    4. Detect lat/lng from CF-IPLatitude / CF-IPLongitude if not sent by client.
    """
    city = user_city.strip() if user_city else (request.headers.get("CF-IPCity") or request.headers.get("X-Client-City") or None)
    district = user_district.strip() if user_district else None
    country = request.headers.get("CF-IPCountry") or request.headers.get("X-Country-Code") or "Sri Lanka"

    lat = user_lat
    lng = user_lng
    if lat is None and request.headers.get("CF-IPLatitude"):
        try:
            lat = float(request.headers.get("CF-IPLatitude"))
            lng = float(request.headers.get("CF-IPLongitude"))
        except (ValueError, TypeError):
            pass

    return city, district, country, lat, lng


class PublicAdmissionSubmission(BaseModel):
    full_name: str
    email: Optional[str] = None
    phone: str
    whatsapp: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    highest_qualification: Optional[str] = None
    interested_programme: Optional[str] = None
    interested_course: Optional[str] = None
    school: Optional[str] = None
    student_status: Optional[str] = None
    parents_occupation: Optional[str] = None
    parents_email: Optional[str] = None
    notes: Optional[str] = None


class PublicContactSubmission(BaseModel):
    full_name: str
    email: Optional[str] = None
    phone: str
    city: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    interested_programme: Optional[str] = None
    message: str


class WhatsAppBotLeadSubmission(BaseModel):
    phone: str
    full_name: Optional[str] = "WhatsApp Lead"
    interested_programme: Optional[str] = None
    interested_course: Optional[str] = None
    city: Optional[str] = None
    last_message: Optional[str] = None
    conversation_summary: Optional[str] = None
    bot_session_id: Optional[str] = None


class MetaLeadSubmission(BaseModel):
    lead_id: Optional[str] = None
    full_name: str
    email: Optional[str] = None
    phone: str
    city: Optional[str] = None
    interested_programme: Optional[str] = None
    ad_id: Optional[str] = None
    form_id: Optional[str] = None
    platform: Optional[str] = "meta"  # meta, instagram, facebook


@router.post("/admission", status_code=201)
async def submit_website_admission(
    payload: PublicAdmissionSubmission,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Public intake for Website Admission Form.
    Silently extracts city & country via IP / Cloudflare headers if not manually entered.
    """
    city, district, country, lat, lng = extract_client_location(
        request,
        user_city=payload.city,
        user_district=payload.district,
        user_lat=payload.location_lat,
        user_lng=payload.location_lng,
    )

    create_payload = CrmLeadCreate(
        full_name=payload.full_name.strip(),
        email=payload.email.strip().lower() if payload.email else None,
        phone=payload.phone.strip(),
        whatsapp=payload.whatsapp.strip() if payload.whatsapp else payload.phone.strip(),
        city=city,
        district=district,
        country=country,
        location_lat=lat,
        location_lng=lng,
        highest_qualification=payload.highest_qualification,
        interested_programme=payload.interested_programme,
        interested_course=payload.interested_course,
        school=payload.school,
        student_status=payload.student_status,
        parents_occupation=payload.parents_occupation,
        parents_email=payload.parents_email,
        source="website_admission",
        stage="new_inquiry",
        priority="high" if payload.highest_qualification in ("A/L", "Degree", "Diploma") else "medium",
        notes=payload.notes,
    )
    lead = await service.create_lead(
        db,
        create_payload,
        counsellor_id=None,
        counsellor_name="Website Admission Form",
    )
    return {
        "status": "success",
        "lead_id": lead.lead_id,
        "message": "Thank you! Your admission inquiry has been received by Inspire College Admissions.",
    }


@router.post("/contact", status_code=201)
async def submit_website_contact(
    payload: PublicContactSubmission,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Public intake for Website General Contact Form.
    Silently extracts city & country via IP / Cloudflare headers if not manually entered.
    """
    city, district, country, lat, lng = extract_client_location(
        request,
        user_city=payload.city,
        user_lat=payload.location_lat,
        user_lng=payload.location_lng,
    )

    create_payload = CrmLeadCreate(
        full_name=payload.full_name.strip(),
        email=payload.email.strip().lower() if payload.email else None,
        phone=payload.phone.strip(),
        city=city,
        country=country,
        location_lat=lat,
        location_lng=lng,
        interested_programme=payload.interested_programme,
        message=payload.message.strip(),
        source="website_contact",
        stage="new_inquiry",
        priority="medium",
    )
    lead = await service.create_lead(
        db,
        create_payload,
        counsellor_id=None,
        counsellor_name="Website Contact Form",
    )
    return {
        "status": "success",
        "lead_id": lead.lead_id,
        "message": "Thank you for contacting Inspire College. A counsellor will reach out shortly.",
    }


@router.post("/integrations/whatsapp", status_code=201)
async def submit_whatsapp_lead(
    payload: WhatsAppBotLeadSubmission,
    x_webhook_secret: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Webhook intake for WhatsApp Bot System.
    When a student chats with the bot and expresses interest in a programme,
    the bot submits the lead into Inspire CRM.
    """
    notes_parts = []
    if payload.bot_session_id:
        notes_parts.append(f"WhatsApp Session: {payload.bot_session_id}")
    if payload.conversation_summary:
        notes_parts.append(f"Bot Summary: {payload.conversation_summary}")

    create_payload = CrmLeadCreate(
        full_name=payload.full_name or "WhatsApp Prospect",
        phone=payload.phone.strip(),
        whatsapp=payload.phone.strip(),
        city=payload.city,
        interested_programme=payload.interested_programme,
        interested_course=payload.interested_course,
        message=payload.last_message,
        source="whatsapp",
        stage="new_inquiry",
        priority="medium",
        notes="\n".join(notes_parts) if notes_parts else None,
    )
    lead = await service.create_lead(
        db,
        create_payload,
        counsellor_id=None,
        counsellor_name="WhatsApp Bot",
    )
    return {
        "status": "success",
        "lead_id": lead.lead_id,
        "message": "WhatsApp lead recorded in Inspire CRM.",
    }


@router.post("/integrations/meta", status_code=201)
async def submit_meta_lead(
    payload: MetaLeadSubmission,
    db: AsyncSession = Depends(get_db),
):
    """
    Ready webhook intake for Meta Lead Ads (Facebook & Instagram).
    """
    source_name = "instagram" if payload.platform == "instagram" else "meta"
    create_payload = CrmLeadCreate(
        full_name=payload.full_name.strip(),
        email=payload.email.strip().lower() if payload.email else None,
        phone=payload.phone.strip(),
        city=payload.city,
        interested_programme=payload.interested_programme,
        source=source_name,
        stage="new_inquiry",
        priority="medium",
        social_lead_id=payload.lead_id,
        notes=f"Meta Ad Form ID: {payload.form_id or 'N/A'}, Ad ID: {payload.ad_id or 'N/A'}",
    )
    lead = await service.create_lead(
        db,
        create_payload,
        counsellor_id=None,
        counsellor_name=f"Meta Ads ({payload.platform})",
    )
    return {
        "status": "success",
        "lead_id": lead.lead_id,
        "message": "Meta lead recorded in Inspire CRM.",
    }
