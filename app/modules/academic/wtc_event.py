import html
import logging
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.core.public_form_email import FormEmailResult, form_email_html, send_public_form_email

logger = logging.getLogger(__name__)
WTC_TAG = "WTC registrations form"
ENROL_EMAIL = "enrol@inspire.college"


class WtcRegistrationCreate(BaseModel):
    submission_id: UUID
    full_name: str = Field(..., min_length=2, max_length=255)
    phone: str = Field(..., min_length=7, max_length=50)
    email: EmailStr
    company_name: str = Field(..., min_length=2, max_length=255)
    employee_id: str | None = Field(None, max_length=100)
    programme: Literal["CPD Short Courses", "ATHE Level 7 Qualifications", "Jain University Master’s", "Not Sure Yet"]
    visiting_time: Literal["8.30 AM – 10.00 AM", "10.00 AM – 12.00 PM", "12.00 PM – 2.00 PM", "2.00 PM – 4.00 PM", "4.00 PM – 5.30 PM"]
    consent: Literal[True]

    @field_validator("full_name", "phone", "company_name", "employee_id", mode="before")
    @classmethod
    def strip_fields(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value):
        if not 7 <= sum(char.isdigit() for char in value) <= 15:
            raise ValueError("Enter a valid mobile or WhatsApp number")
        return value


class WtcRegistrationResponse(BaseModel):
    registration_id: str
    confirmation_sent: bool
    notification_sent: bool
    lead_id: int
    crm_saved: Literal[True]
    message: str


WTC_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wtc_event_registrations (
    submission_id UUID PRIMARY KEY,
    registration_id VARCHAR(30) NOT NULL UNIQUE,
    email VARCHAR(320) NOT NULL UNIQUE,
    lead_id INTEGER NOT NULL REFERENCES crm_leads(lead_id),
    full_name VARCHAR(255) NOT NULL, phone VARCHAR(50) NOT NULL,
    company_name VARCHAR(255) NOT NULL, employee_id VARCHAR(100),
    programme VARCHAR(100) NOT NULL, visiting_time VARCHAR(100) NOT NULL,
    consent_version VARCHAR(50) NOT NULL DEFAULT 'wtc-2026-10-26-v1',
    consent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmation_sent BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def registration_email(payload: WtcRegistrationCreate, registration_id: str):
    details = [("Registration ID", registration_id), ("Date", "26th October 2026"),
               ("Event hours", "8.30 AM to 5.30 PM"), ("Venue", "World Trade Center, Colombo"),
               ("Preferred visit", payload.visiting_time), ("Programme", payload.programme), ("Company", payload.company_name)]
    plain = (f"Hi {payload.full_name},\n\nYou’re Registered!\nThank you for registering for Inspire College at WTC.\n\n"
             + "\n".join(f"{label}: {value}" for label, value in details)
             + "\n\nWe look forward to meeting you!\nRegistration is free. Discounts apply to selected programmes only.\n\nInspire College")
    rows = "".join(f'<tr><td style="padding:10px 0;color:#6b5d7d;font-size:13px">{html.escape(label)}</td><td style="padding:10px 0;text-align:right;color:#25113d;font-size:13px;font-weight:bold">{html.escape(value)}</td></tr>' for label, value in details)
    markup = (
        '<!doctype html><html><body style="margin:0;padding:24px 12px;background:#f7f3fc;font-family:Arial,sans-serif">'
        '<table role="presentation" style="max-width:600px;width:100%;margin:auto;border-spacing:0;background:white;border-radius:18px;overflow:hidden">'
        '<tr><td style="padding:30px;background:#480084;color:white"><div style="font-size:24px;font-weight:bold">inspire college</div>'
        '<p style="font-size:12px;letter-spacing:2px">WTC EVENT · 26 OCTOBER 2026</p><h1 style="margin:12px 0 0;font-size:32px">You’re Registered!</h1></td></tr>'
        f'<tr><td style="padding:28px;color:#25113d;line-height:1.6"><p>Hi {html.escape(payload.full_name)},</p><p>Thank you for registering for Inspire College at WTC.</p>'
        f'<table role="presentation" style="width:100%;border-collapse:collapse">{rows}</table>'
        '<p style="font-size:19px;font-weight:bold;color:#480084">We look forward to meeting you!</p>'
        '<p style="font-size:12px;color:#6b5d7d">Registration is free. Discounts apply to selected programmes only.</p>'
        '<p style="font-size:12px">Reply to this email to reach our enrolments team.</p></td></tr></table></body></html>'
    )
    return plain, markup


def registration_notification(payload: WtcRegistrationCreate, registration_id: str, lead_id: int):
    rows = [
        ("Registration ID", registration_id), ("CRM lead ID", str(lead_id)), ("Tag", WTC_TAG),
        ("Event", "WTC Activation — 26 October 2026"), ("Name", payload.full_name),
        ("Email", str(payload.email)), ("Mobile / WhatsApp", payload.phone),
        ("Company", payload.company_name), ("Employee ID", payload.employee_id or "Not provided"),
        ("Programme of interest", payload.programme), ("Preferred visiting time", payload.visiting_time),
    ]
    intro = "A WTC event registration is ready to review. Please check the registrant’s details and eligibility, and follow up in CRM."
    plain = "WTC new registration\n\n" + intro + "\n\n" + "\n".join(f"{label}: {value}" for label, value in rows)
    return plain, form_email_html("WTC new registration", intro, rows)


def registration_notes(payload: WtcRegistrationCreate, registration_id: str):
    return (f"Tag: {WTC_TAG}\nRegistration ID: {registration_id}\nEvent: WTC Activation — 26 October 2026\n"
            f"Company: {payload.company_name}\nEmployee ID: {payload.employee_id or 'Not provided'}\n"
            f"Programme: {payload.programme}\nPreferred visit: {payload.visiting_time}\n"
            "Consent: event registration and eligibility verification; privacy notice wtc-2026-10-26-v1 accepted.")


async def _safe_wtc_email(**kwargs):
    try:
        return await send_public_form_email(**kwargs)
    except Exception:
        logger.exception("WTC email transport failed for %s", kwargs.get("custom_id"))
        return FormEmailResult(False, "Email transport failed")


async def register_wtc_visitor(db: AsyncSession, payload: WtcRegistrationCreate):
    from app.modules.crm.service import _ensure_counsellor_status_table, apply_auto_assignment
    email = str(payload.email).lower().strip()
    registration_id = f"WTC26-{uuid4().hex[:8].upper()}"
    values = {
        "submission_id": str(payload.submission_id), "email": email, "full_name": payload.full_name,
        "phone": payload.phone, "company_name": payload.company_name, "employee_id": payload.employee_id or None,
        "programme": payload.programme, "visiting_time": payload.visiting_time,
    }
    try:
        await _ensure_counsellor_status_table(db)
        await db.execute(text(WTC_TABLE_SQL))
        await db.execute(text("""ALTER TABLE wtc_event_registrations
            ADD COLUMN IF NOT EXISTS notification_sent BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS confirmation_error TEXT,
            ADD COLUMN IF NOT EXISTS notification_error TEXT,
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """))
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:email))"), {"email": f"wtc26:{email}"})
        existing = (await db.execute(text("SELECT * FROM wtc_event_registrations WHERE email=:email OR submission_id=:submission_id FOR UPDATE"), values)).mappings().first()
        if existing:
            registration_id = existing["registration_id"]
            lead_id = existing["lead_id"]
            changed = any((existing[key] or None) != (values[key] or None) for key in ("email", "full_name", "phone", "company_name", "employee_id", "programme", "visiting_time"))
        else:
            changed = True
        notes = registration_notes(payload, registration_id)
        params = {**values, "registration_id": registration_id, "notes": notes, "message": WTC_TAG}
        if not existing:
            lead = (await db.execute(text("""
                INSERT INTO crm_leads (full_name, email, phone, whatsapp, interested_programme, campaign, source, stage, priority, notes, message, is_archived)
                VALUES (:full_name, :email, :phone, :phone, :programme, 'WTC Activation — 26 October 2026', 'wtc_registrations_form', 'new_lead', 'high', :notes, :message, FALSE)
                RETURNING lead_id
            """), params)).mappings().one()
            lead_id = lead["lead_id"]
            params["lead_id"] = lead_id
            await db.execute(text("""
                INSERT INTO wtc_event_registrations (submission_id, registration_id, email, lead_id, full_name, phone, company_name, employee_id, programme, visiting_time)
                VALUES (:submission_id, :registration_id, :email, :lead_id, :full_name, :phone, :company_name, :employee_id, :programme, :visiting_time)
            """), params)
            await apply_auto_assignment(db, lead_id, commit=False)
        elif changed:
            params["lead_id"] = lead_id
            lead = (await db.execute(text("""
                UPDATE crm_leads SET full_name=:full_name, email=:email, phone=:phone, whatsapp=:phone,
                    interested_programme=:programme, campaign='WTC Activation — 26 October 2026',
                    notes=coalesce(notes, '') || E'\\n\\nWTC registration updated\\n' || :notes,
                    message=:message, updated_at=now()
                WHERE lead_id=:lead_id RETURNING lead_id
            """), params)).mappings().first()
            if not lead:
                raise RuntimeError("The linked CRM lead is missing")
            await db.execute(text("""
                UPDATE wtc_event_registrations SET email=:email, full_name=:full_name, phone=:phone,
                    company_name=:company_name, employee_id=:employee_id, programme=:programme,
                    visiting_time=:visiting_time, confirmation_sent=FALSE, notification_sent=FALSE,
                    confirmation_error=NULL, notification_error=NULL, updated_at=now(), consent_at=now()
                WHERE registration_id=:registration_id
            """), params)
        if changed:
            await db.execute(text("INSERT INTO crm_activities (lead_id, activity_type, content) VALUES (:lead_id, 'note', :content)"), {"lead_id": lead_id, "content": notes})
            await db.execute(text("UPDATE crm_leads SET last_activity_at=now() WHERE lead_id=:lead_id"), {"lead_id": lead_id})
        # The popup can only be returned after the registration AND CRM changes commit.
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("WTC registration could not be saved")
        raise APIError(503, "WTC_SAVE_FAILED", "We could not save your registration. Please try again.") from exc

    confirmation_sent = False
    notification_sent = False
    try:
        # Re-read after locking so simultaneous retries do not resend accepted emails.
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:email))"), {"email": f"wtc26:{email}"})
        delivery = (await db.execute(text("SELECT confirmation_sent, notification_sent FROM wtc_event_registrations WHERE registration_id=:registration_id FOR UPDATE"), {"registration_id": registration_id})).mappings().one()
        confirmation_sent = delivery["confirmation_sent"]
        notification_sent = delivery["notification_sent"]
        if not confirmation_sent:
            plain, markup = registration_email(payload, registration_id)
            result = await _safe_wtc_email(
                subject=f"WTC Event — You’re Registered! | {registration_id}", text_body=plain, html_body=markup,
                to_email=email, to_name=payload.full_name, reply_to=ENROL_EMAIL, custom_id=f"wtc-confirmation-{registration_id}",
            )
            confirmation_sent = result.sent
            await db.execute(text("UPDATE wtc_event_registrations SET confirmation_sent=:sent, confirmation_error=:error WHERE registration_id=:registration_id"), {"sent": result.sent, "error": result.error, "registration_id": registration_id})
        if not notification_sent:
            plain, markup = registration_notification(payload, registration_id, lead_id)
            result = await _safe_wtc_email(
                subject=f"WTC new registration — {payload.full_name} | {registration_id}", text_body=plain, html_body=markup,
                to_email=ENROL_EMAIL, to_name="Inspire College Enrolments", reply_to=email,
                custom_id=f"wtc-notification-{registration_id}",
            )
            notification_sent = result.sent
            await db.execute(text("UPDATE wtc_event_registrations SET notification_sent=:sent, notification_error=:error WHERE registration_id=:registration_id"), {"sent": result.sent, "error": result.error, "registration_id": registration_id})
        await db.commit()
        if not confirmation_sent or not notification_sent:
            logger.warning("WTC %s saved; email acceptance: confirmation=%s, notification=%s", registration_id, confirmation_sent, notification_sent)
    except Exception:
        await db.rollback()
        logger.exception("WTC registration %s saved but email processing failed", registration_id)
    return {"registration_id": registration_id, "lead_id": lead_id, "crm_saved": True, "confirmation_sent": confirmation_sent,
            "notification_sent": notification_sent, "message": "You’re Registered!"}
