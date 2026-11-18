"""Conditional offer letters and the documents students upload through the offer link."""
import asyncio
import calendar
import logging
import re
import secrets
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import APIError, NotFoundError, ValidationError
from app.core.public_form_email import form_email_html, offer_letter_email_content, send_public_form_email

logger = logging.getLogger(__name__)
COLOMBO = ZoneInfo("Asia/Colombo")
OFFER_VALID_MONTHS = 2
DOCUMENT_TYPES = {
    "nic_passport": "NIC or Passport",
    "qualification": "Highest Educational Qualification",
    "transcript": "Academic Transcript",
}
_SQL_FILE = Path(__file__).parent / "sql" / "2026_11_18_offer_letters.sql"
_tables_ready = False


def add_months(value: date, months: int) -> date:
    """Same day N months later, clamped to the month's last day (31 Dec + 2 = 28/29 Feb)."""
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def format_offer_date(value: date) -> str:
    return f"{value.day} {value.strftime('%B %Y')}"


def offer_expiry(sent_at: datetime) -> tuple[date, datetime]:
    """The expiry date shown to the student, and the moment the link stops working (end of that day, Colombo)."""
    expiry_day = add_months(sent_at.astimezone(COLOMBO).date(), OFFER_VALID_MONTHS)
    return expiry_day, datetime.combine(expiry_day, time(23, 59, 59), COLOMBO).astimezone(timezone.utc)


def offer_link(token: str) -> str:
    return f"{settings.PUBLIC_WEBSITE_URL.rstrip('/')}/offer-documents/{token}"


def lead_programme(lead) -> str | None:
    return ((lead.get("interested_course") or lead.get("interested_programme") or "").strip()) or None


def missing_offer_fields(lead) -> list[str]:
    missing = []
    if not (lead.get("full_name") or "").strip():
        missing.append("full name")
    if not (lead.get("email") or "").strip():
        missing.append("email")
    if not lead_programme(lead):
        missing.append("programme")
    return missing


async def ensure_offer_tables(db: AsyncSession) -> None:
    global _tables_ready
    if _tables_ready:
        return
    for statement in _SQL_FILE.read_text(encoding="utf-8").split(";"):
        lines = [line for line in statement.splitlines() if line.strip() and not line.strip().startswith("--")]
        if lines:
            await db.execute(text("\n".join(lines)))
    await db.commit()
    _tables_ready = True


async def _lead(db: AsyncSession, lead_id: int) -> dict:
    row = (await db.execute(text("""
        SELECT lead_id, full_name, email, interested_programme, interested_course, assigned_counsellor_id
        FROM crm_leads WHERE lead_id=:id
    """), {"id": lead_id})).mappings().first()
    if row is None:
        raise NotFoundError(f"Lead {lead_id} not found")
    return dict(row)


async def received_document_types(db: AsyncSession, lead_id: int) -> set[str]:
    """Document types this lead has already given us, so the offer email does not ask for them again."""
    rows = await db.execute(text("SELECT DISTINCT document_type FROM crm_lead_documents WHERE lead_id=:id"), {"id": lead_id})
    return {row[0] for row in rows.all()}


async def record_existing_document(db: AsyncSession, lead_id: int, document_type: str, s3_key: str, file_name: str, content_type: str, size_bytes: int) -> None:
    """Link a document the student already uploaded elsewhere (the website admission form) to the lead."""
    await ensure_offer_tables(db)
    await db.execute(text("""
        INSERT INTO crm_lead_documents (lead_id, document_type, s3_key, file_name, content_type, size_bytes)
        VALUES (:lead_id, :type, :key, :name, :content_type, :size)
    """), {"lead_id": lead_id, "type": document_type, "key": s3_key, "name": file_name[:255],
           "content_type": content_type, "size": size_bytes})
    await db.commit()


async def send_offer_letter(db: AsyncSession, lead_id: int, *, sent_by: int | None = None, sent_by_name: str | None = None, origin: str = "crm") -> dict:
    """Create a new offer (fresh link, 2-month expiry), email it and log it on the timeline. The stage is not changed."""
    await ensure_offer_tables(db)
    lead = await _lead(db, lead_id)
    missing = missing_offer_fields(lead)
    if missing:
        raise ValidationError(f"Add the lead's {', '.join(missing)} before sending the offer letter")
    full_name = lead["full_name"].strip()
    first_name = full_name.split()[0]
    programme = lead_programme(lead)
    sent_at = datetime.now(timezone.utc)
    expiry_day, expires_at = offer_expiry(sent_at)
    token = secrets.token_urlsafe(24)
    offer_id = await db.scalar(text("""
        INSERT INTO crm_offer_letters (lead_id, token, programme, sent_at, expires_at, sent_by_user_id, origin)
        VALUES (:lead_id, :token, :programme, :sent_at, :expires_at, :sent_by, :origin) RETURNING offer_id
    """), {"lead_id": lead_id, "token": token, "programme": programme, "sent_at": sent_at,
           "expires_at": expires_at, "sent_by": sent_by, "origin": origin})
    await db.commit()

    received = await received_document_types(db, lead_id)
    subject, text_body, html_body = offer_letter_email_content(first_name, full_name, programme, offer_link(token), format_offer_date(expiry_day), received)
    try:
        result = await send_public_form_email(
            subject, text_body, html_body, reply_to=settings.PUBLIC_FORM_FROM_EMAIL,
            custom_id=f"offer-{offer_id}", to_email=lead["email"].strip(), to_name=full_name,
        )
        sent, error = result.sent, result.error
    except Exception:
        logger.exception("Offer letter %s for lead %s failed unexpectedly", offer_id, lead_id)
        sent, error = False, "The email could not be sent"
    await db.execute(text("UPDATE crm_offer_letters SET email_status=:status, email_error=:error WHERE offer_id=:id"),
                     {"status": "sent" if sent else "failed", "error": None if sent else (error or "Email provider rejected the message"), "id": offer_id})
    content = (f"Conditional offer letter sent to {lead['email'].strip()} for {programme} (valid until {format_offer_date(expiry_day)})."
               if sent else f"Conditional offer letter could not be sent: {error or 'email provider rejected the message'}.")
    if origin == "website_admission":
        content += " Sent automatically after the website application."
    await db.execute(text("""
        INSERT INTO crm_activities (lead_id, activity_type, content, counsellor_id, counsellor_name)
        VALUES (:lead_id, 'email', :content, :counsellor_id, :counsellor_name)
    """), {"lead_id": lead_id, "content": content, "counsellor_id": sent_by, "counsellor_name": sent_by_name})
    await db.execute(text("UPDATE crm_leads SET last_activity_at=now() WHERE lead_id=:id"), {"id": lead_id})
    await db.commit()
    if not sent:
        logger.warning("Offer letter %s for lead %s was not delivered: %s", offer_id, lead_id, error)
    return await offer_summary(db, lead_id)


def _iso(value):
    return value.isoformat() if value else None


async def offer_summary(db: AsyncSession, lead_id: int) -> dict:
    """Latest offer for the CRM lead page, plus every uploaded document (newest first)."""
    await ensure_offer_tables(db)
    lead = await _lead(db, lead_id)
    offer = (await db.execute(text("""
        SELECT offer_id, programme, sent_at, expires_at, email_status, email_error, origin
        FROM crm_offer_letters WHERE lead_id=:id ORDER BY sent_at DESC, offer_id DESC LIMIT 1
    """), {"id": lead_id})).mappings().first()
    documents = (await db.execute(text("""
        SELECT document_id, document_type, file_name, content_type, size_bytes, uploaded_at
        FROM crm_lead_documents WHERE lead_id=:id ORDER BY uploaded_at DESC, document_id DESC
    """), {"id": lead_id})).mappings().all()
    now = datetime.now(timezone.utc)
    return {
        "missing_fields": missing_offer_fields(lead),
        "offer": None if offer is None else {
            "offer_id": offer["offer_id"],
            "programme": offer["programme"],
            "sent_at": _iso(offer["sent_at"]),
            "expires_at": _iso(offer["expires_at"]),
            "expired": offer["expires_at"] <= now,
            "email_status": offer["email_status"],
            "email_error": offer["email_error"],
            "origin": offer["origin"],
        },
        "documents": [{
            "document_id": d["document_id"],
            "document_type": d["document_type"],
            "document_label": DOCUMENT_TYPES.get(d["document_type"], d["document_type"]),
            "file_name": d["file_name"],
            "content_type": d["content_type"],
            "size_bytes": d["size_bytes"],
            "uploaded_at": _iso(d["uploaded_at"]),
        } for d in documents],
    }


async def lead_document_url(db: AsyncSession, lead_id: int, document_id: int) -> dict:
    """A 5-minute private link that opens the uploaded document in the browser."""
    from app.modules.cms import media_service

    await ensure_offer_tables(db)
    row = (await db.execute(text("""
        SELECT s3_key, file_name, content_type FROM crm_lead_documents WHERE lead_id=:lead_id AND document_id=:id
    """), {"lead_id": lead_id, "id": document_id})).mappings().first()
    if row is None:
        raise NotFoundError("Document not found")
    safe_name = re.sub(r"[^\x20-\x7e]", "_", row["file_name"].replace("\\", "/").split("/")[-1]).replace('"', "_")
    try:
        url = await asyncio.to_thread(
            media_service._client().generate_presigned_url,
            "get_object",
            Params={
                "Bucket": settings.MEDIA_BUCKET,
                "Key": row["s3_key"],
                "ResponseContentType": row["content_type"],
                "ResponseContentDisposition": f'inline; filename="{safe_name}"',
            },
            ExpiresIn=300,
        )
    except Exception as exc:
        raise APIError(503, "DOCUMENT_UNAVAILABLE", "The document is temporarily unavailable") from exc
    return {"name": row["file_name"], "url": url}


async def _offer_by_token(db: AsyncSession, token: str):
    if not token or len(token) > 64:
        return None
    return (await db.execute(text("""
        SELECT o.offer_id, o.lead_id, o.programme, o.expires_at, l.full_name, l.assigned_counsellor_id
        FROM crm_offer_letters o JOIN crm_leads l ON l.lead_id=o.lead_id
        WHERE o.token=:token
    """), {"token": token})).mappings().first()


async def public_offer_view(db: AsyncSession, token: str) -> dict:
    """What the website upload page may show. Nothing beyond first name, programme and this offer's uploads."""
    await ensure_offer_tables(db)
    offer = await _offer_by_token(db, token)
    if offer is None:
        return {"status": "not_found"}
    expiry_day = offer["expires_at"].astimezone(COLOMBO).date()
    view = {
        "status": "expired" if offer["expires_at"] <= datetime.now(timezone.utc) else "active",
        "first_name": ((offer["full_name"] or "").split() or ["there"])[0],
        "programme": offer["programme"],
        "expires_on": format_offer_date(expiry_day),
    }
    uploads = (await db.execute(text("""
        SELECT document_type, file_name, uploaded_at FROM crm_lead_documents
        WHERE lead_id=:id ORDER BY uploaded_at DESC, document_id DESC
    """), {"id": offer["lead_id"]})).mappings().all()
    view["uploaded"] = [{"document_type": u["document_type"], "file_name": u["file_name"], "uploaded_at": _iso(u["uploaded_at"])} for u in uploads]
    return view


async def _notify_staff(db: AsyncSession, offer, label: str, file_name: str) -> None:
    recipient, name = None, "Inspire Admissions"
    if offer["assigned_counsellor_id"]:
        user = (await db.execute(text("SELECT email, full_name FROM users WHERE user_id=:id"),
                                 {"id": offer["assigned_counsellor_id"]})).mappings().first()
        if user and (user["email"] or "").strip():
            recipient, name = user["email"].strip(), user["full_name"] or user["email"]
    student = (offer["full_name"] or "A student").strip()
    rows = [("Student", student), ("Lead ID", str(offer["lead_id"])), ("Document", label), ("File", file_name), ("Programme", offer["programme"])]
    subject = f"Offer document received — {student} ({label})"
    text_body = "A student uploaded an offer document.\n\n" + "\n".join(f"{k}: {v}" for k, v in rows) + "\n\nOpen the lead in the CRM to view it."
    try:
        result = await send_public_form_email(
            subject, text_body,
            form_email_html("Offer document received", "A student uploaded a document through their offer link. Open the lead in the CRM to view it.", rows),
            custom_id=f"offer-doc-{offer['offer_id']}", to_email=recipient, to_name=name,
        )
        if not result.sent:
            logger.warning("Offer document notification for lead %s failed: %s", offer["lead_id"], result.error)
    except Exception:
        logger.exception("Offer document notification for lead %s failed unexpectedly", offer["lead_id"])


async def public_upload_document(db: AsyncSession, token: str, payload) -> dict:
    """Store one uploaded document privately, link it to the lead, log it and notify staff."""
    from app.modules.academic.service import store_private_document
    from app.modules.cms import media_service

    await ensure_offer_tables(db)
    offer = await _offer_by_token(db, token)
    if offer is None:
        raise APIError(404, "OFFER_NOT_FOUND", "This offer link is not valid. Please contact admissions on 071 199 3331.")
    if offer["expires_at"] <= datetime.now(timezone.utc):
        raise APIError(410, "OFFER_EXPIRED", "This offer has expired. Please contact admissions on 071 199 3331.")
    label = DOCUMENT_TYPES[payload.document_type]
    key = await store_private_document(payload, f"admissions/offer-documents/{offer['lead_id']}", label=label.lower())
    file_name = re.sub(r"[\r\n]", "", payload.filename.replace("\\", "/").split("/")[-1])[:255] or "document"
    try:
        await db.execute(text("""
            INSERT INTO crm_lead_documents (lead_id, offer_id, document_type, s3_key, file_name, content_type, size_bytes)
            VALUES (:lead_id, :offer_id, :type, :key, :name, :content_type, :size)
        """), {"lead_id": offer["lead_id"], "offer_id": offer["offer_id"], "type": payload.document_type, "key": key,
               "name": file_name, "content_type": payload.content_type, "size": payload.size_bytes})
        await db.execute(text("""
            INSERT INTO crm_activities (lead_id, activity_type, content)
            VALUES (:lead_id, 'document_received', :content)
        """), {"lead_id": offer["lead_id"], "content": f"{label} uploaded through the offer link ({file_name})."})
        await db.execute(text("UPDATE crm_leads SET last_activity_at=now() WHERE lead_id=:id"), {"id": offer["lead_id"]})
        await db.commit()
    except Exception:
        await db.rollback()
        try:
            await asyncio.to_thread(media_service._client().delete_object, Bucket=settings.MEDIA_BUCKET, Key=key)
        except Exception:
            pass
        raise
    await _notify_staff(db, offer, label, file_name)
    return await public_offer_view(db, token)
