"""CRM leads from the new website. Every new lead gets the source 'new_website' (shown as "New website" in the CRM).

One person = one lead: a visitor is matched to an open lead by phone (normalised) or email. A match gets an activity
note and only its empty fields filled; it keeps its original source and counsellor.
"""
import logging
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.crm import service as crm_service
from app.modules.crm.schemas import CrmLeadCreate

logger = logging.getLogger(__name__)
SOURCE = "new_website"
ACTOR = "New website"

KIND_NOTE = {
    "checkout_started": "Started enrolling on the new website",
    "order": "Placed an enrolment order on the new website",
    "contact": "Sent the contact form on the new website",
    "admissions": "Sent the admissions form on the new website",
    "enrol": "Asked about enrolling on the new website",
    "aptitude": "Used the programme finder on the new website",
    "callback": "Asked for a callback on the new website",
    "demo": "Signed up for the free demo class on the new website",
}


def norm_phone(value: Optional[str]) -> str:
    return crm_service._normalized_lead_phone(value)


async def _find(db: AsyncSession, email: str, phone: str) -> Optional[dict]:
    last9 = norm_phone(phone)[-9:] if phone else ""
    if not email and len(last9) < 9:
        return None
    row = (await db.execute(text("""
        SELECT lead_id, full_name, email, phone, whatsapp, academic_course_id, stage, source
        FROM crm_leads
        WHERE is_archived = false
          AND ((:email <> '' AND lower(email) = :email)
               OR (:last9 <> '' AND right(regexp_replace(coalesce(phone, ''), '\\D', '', 'g'), 9) = :last9)
               OR (:last9 <> '' AND right(regexp_replace(coalesce(whatsapp, ''), '\\D', '', 'g'), 9) = :last9))
        ORDER BY (lower(coalesce(email, '')) = :email) DESC, created_at DESC
        LIMIT 1
    """), {"email": email, "last9": last9})).mappings().first()
    return dict(row) if row else None


async def activity(db: AsyncSession, lead_id: int, content: str, kind: str = "note") -> None:
    await db.execute(text("""INSERT INTO crm_activities (lead_id, activity_type, content, counsellor_id, counsellor_name)
        VALUES (:id, :kind, :content, NULL, :actor)"""), {"id": lead_id, "kind": kind, "content": content[:2000], "actor": ACTOR})
    await db.execute(text("UPDATE crm_leads SET last_activity_at = now() WHERE lead_id = :id"), {"id": lead_id})


async def upsert(db: AsyncSession, *, kind: str, name: str, email: str = "", phone: str = "", course_id: Optional[int] = None,
                 message: str = "", plan: str = "", programme_fee: Optional[float] = None, registration_fee: Optional[float] = None,
                 utm: Optional[dict] = None, referral: str = "", extra_note: str = "") -> int:
    """Create or update the visitor's lead and return its id. Commits."""
    email = (email or "").strip().lower()[:200]
    phone = (phone or "").strip()[:50]
    name = (name or "").strip()[:200] or "Website visitor"
    key = norm_phone(phone)[-9:] or email
    if key:
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "web-lead:" + key})
    note = KIND_NOTE.get(kind, "New website activity") + (f": {extra_note}" if extra_note else "") + "."
    found = await _find(db, email, phone)
    campaign = None
    if utm:
        campaign = " / ".join(str(utm.get(k)) for k in ("utm_source", "utm_medium", "utm_campaign") if utm.get(k))[:255] or None
    if found:
        lead_id = found["lead_id"]
        await db.execute(text("""UPDATE crm_leads SET
              email = COALESCE(NULLIF(email, ''), NULLIF(:email, '')),
              phone = COALESCE(NULLIF(phone, ''), NULLIF(:phone, '')),
              whatsapp = COALESCE(NULLIF(whatsapp, ''), NULLIF(:phone, '')),
              payment_plan = CASE WHEN :plan <> '' THEN :plan ELSE payment_plan END,
              referral_code = COALESCE(referral_code, NULLIF(:ref, '')),
              campaign = COALESCE(campaign, CAST(:campaign AS varchar)),
              programme_fee = COALESCE(programme_fee, CAST(:fee AS numeric)),
              registration_fee = COALESCE(registration_fee, CAST(:reg AS numeric)),
              updated_at = now()
            WHERE lead_id = :id"""), {"email": email, "phone": phone, "plan": plan[:500], "ref": (referral or "")[:40], "id": lead_id,
                                       "campaign": campaign, "fee": programme_fee, "reg": registration_fee})
        if course_id and not found.get("academic_course_id"):
            try:
                data: dict = {"academic_course_id": course_id}
                crm_service._apply_course_choice(data, await crm_service._resolve_course(db, course_id))
                await db.execute(text("""UPDATE crm_leads SET academic_course_id=:academic_course_id, interested_programme=:interested_programme,
                    interested_course=:interested_course, awarding_body=:awarding_body WHERE lead_id=:id"""),
                                 {**{k: data.get(k) for k in ("academic_course_id", "interested_programme", "interested_course", "awarding_body")}, "id": lead_id})
            except Exception:
                logger.info("Course %s could not be set on lead %s", course_id, lead_id)
        await activity(db, lead_id, note + (f"\nMessage: {message.strip()}" if message.strip() else ""))
        await db.commit()
        return lead_id
    payload = CrmLeadCreate(
        full_name=name, email=email or None, phone=phone or None, whatsapp=phone or None, source=SOURCE, stage="new_lead",
        priority="high" if kind in ("order", "checkout_started") else "medium", message=message.strip()[:4000] or None,
        academic_course_id=course_id, payment_plan=plan[:500] or None, programme_fee=programme_fee, registration_fee=registration_fee,
        campaign=campaign, referral_code=(referral or "").strip()[:40] or None, notes=note,
    )
    try:
        lead = await crm_service.create_lead(db, payload, counsellor_id=None, counsellor_name=ACTOR)
    except Exception:
        # An inactive course must never lose the lead.
        await db.rollback()
        payload.academic_course_id = None
        lead = await crm_service.create_lead(db, payload, counsellor_id=None, counsellor_name=ACTOR)
    return lead.lead_id


async def send_offer_once(db: AsyncSession, lead_id: int) -> None:
    """Conditional offer email after a website order (at most once every 30 days per lead)."""
    try:
        from app.modules.crm.offer_service import ensure_offer_tables, send_offer_letter
        await ensure_offer_tables(db)
        recent = await db.scalar(text("""SELECT 1 FROM crm_offer_letters WHERE lead_id=:id AND origin=:o
            AND sent_at > now() - interval '30 days' LIMIT 1"""), {"id": lead_id, "o": SOURCE})
        if not recent:
            await send_offer_letter(db, lead_id, origin=SOURCE)
    except Exception:
        logger.exception("Offer letter for website lead %s failed", lead_id)
        await db.rollback()


async def record_payment(db: AsyncSession, lead_id: int, *, amount: float, paid_on: str, learner_no: str, invoice_numbers: list[str],
                         method: str) -> None:
    """Payment fields and a timeline note on the lead, then move it to Enrolled (which sends the enrolment email)."""
    await db.execute(text("""UPDATE crm_leads SET payment_status='paid', amount_paid=COALESCE(amount_paid, 0) + :amount,
        payment_date=:paid_on, updated_at=now() WHERE lead_id=:id"""), {"amount": amount, "paid_on": date.fromisoformat(str(paid_on)[:10]), "id": lead_id})
    how = "card (PayHere)" if method == "payhere" else "bank transfer"
    await activity(db, lead_id, f"Paid LKR {amount:,.2f} by {how} on the new website. Learner {learner_no}; invoice "
                                f"{', '.join(invoice_numbers)}. Recorded in Accounts.", kind="note")
    await db.commit()
    stage = await db.scalar(text("SELECT stage FROM crm_leads WHERE lead_id=:id"), {"id": lead_id})
    if stage != "enrolled":
        try:
            await crm_service.update_stage(db, lead_id, "enrolled", counsellor_id=None, counsellor_name=ACTOR)
        except Exception:
            logger.exception("Could not move website lead %s to Enrolled", lead_id)
            await db.rollback()
