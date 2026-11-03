"""WhatsApp bot (insbot) -> CRM sync.

The WhatsApp bot qualifies students and hands each one to a counsellor itself.
Once that handover happens the bot pushes the lead here. Rules agreed with the
business:

* The bot's chosen counsellor is recorded as-is. The CRM round robin is never
  run for WhatsApp bot leads (other sources keep using it).
* Only leads that reached a handover are pushed; nothing is backfilled.
* If the phone already belongs to a CRM lead (website, Meta, ...), the bot data
  and chat are merged into that lead instead of creating a duplicate, and the
  existing counsellor is kept.
* Bot leads are keyed by ``external_record_id = "wabot:<supabase lead id>"`` so
  repeated pushes update the same CRM lead.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.crm import service
from app.modules.crm.models.activity import CrmActivity
from app.modules.crm.models.lead import CrmLead
from app.modules.crm.repository import CrmActivityRepository
from app.modules.crm.schemas import (
    CrmCounsellorOut,
    CrmLeadCreate,
    WhatsAppBotLeadSync,
    WhatsAppBotMessageSync,
)

WABOT_PREFIX = "wabot:"
BOT_ACTIVITY_TYPE = "whatsapp_bot"
BOT_ACTOR = "WhatsApp Bot"
SRI_LANKA = timezone(timedelta(hours=5, minutes=30))
MAX_TRANSCRIPT_CHARS = 60_000

PATHWAY_AWARDING_BODY = {
    "uk/athe": "ATHE",
    "athe": "ATHE",
    "jain university": "Jain University",
}

SENDER_LABELS = {"user": "Student", "bot": "Bot", "counselor": "Counsellor", "counsellor": "Counsellor"}


def _clean(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value[:limit] if value else None


def _sl_time(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SRI_LANKA).strftime("%d %b %Y %I:%M %p")


def _external_id(bot_lead_id: str) -> str:
    return f"{WABOT_PREFIX}{bot_lead_id.strip()}"


def _mapped_fields(payload: WhatsAppBotLeadSync) -> dict:
    """Bot lead columns -> CRM lead columns."""
    pathway = _clean(payload.pathway, 100)
    field = _clean(payload.field, 100)
    course = _clean(payload.course_interest, 255)
    programme = course or (f"{field} ({pathway})" if field and pathway else field or pathway)
    return {
        "full_name": _clean(payload.full_name, 255),
        "email": (_clean(payload.email, 255) or "").lower() or None,
        "phone": _clean(payload.phone, 50),
        "whatsapp": _clean(payload.phone, 50),
        "highest_qualification": _clean(payload.qualification, 100),
        "awarding_body": PATHWAY_AWARDING_BODY.get((pathway or "").lower()) if pathway else None,
        "faculty": field,
        "interested_programme": _clean(programme, 255),
        "interested_course": course,
    }


async def _find_counsellor(db: AsyncSession, payload: WhatsAppBotLeadSync) -> Optional[CrmCounsellorOut]:
    """Match the bot's counsellor to a CRM counsellor by email, then by name.

    Counsellors paused in the round robin still match: the bot, not the
    rotation, decides who gets WhatsApp leads.
    """
    bot = payload.counsellor
    if bot is None:
        return None
    rows = service._dedupe_counsellor_rows((await db.execute(service._counsellor_users_stmt(True))).all())
    email = (bot.email or "").strip().lower()
    name = (bot.name or "").strip().lower()
    for user, _status in rows:
        if email and (user.email or "").strip().lower() == email:
            return CrmCounsellorOut(user_id=user.user_id, name=user.full_name or user.email, email=user.email)
    for user, _status in rows:
        if name and (user.full_name or "").strip().lower() == name:
            return CrmCounsellorOut(user_id=user.user_id, name=user.full_name or user.email, email=user.email)
    return None


async def _lead_by_external(db: AsyncSession, external_id: str) -> Optional[CrmLead]:
    return (await db.execute(select(CrmLead).where(CrmLead.external_record_id == external_id))).scalar_one_or_none()


async def _lead_by_phone(db: AsyncSession, phone: Optional[str]) -> Optional[CrmLead]:
    normalized = service._normalized_lead_phone(phone)
    if len(normalized) < 7:
        return None
    suffix = normalized[-9:]
    candidates = (await db.execute(
        select(CrmLead)
        .where(func.right(func.regexp_replace(CrmLead.phone, "[^0-9]", "", "g"), len(suffix)) == suffix)
        .order_by(CrmLead.is_archived.asc(), CrmLead.lead_id.desc())
    )).scalars().all()
    for lead in candidates:
        if service._normalized_lead_phone(lead.phone) == normalized:
            return lead
    return None


async def _activity_exists(db: AsyncSession, lead_id: int, prefix: str) -> bool:
    found = await db.scalar(
        select(func.count()).select_from(CrmActivity).where(
            CrmActivity.lead_id == lead_id,
            CrmActivity.activity_type == BOT_ACTIVITY_TYPE,
            CrmActivity.content.startswith(prefix),
        )
    )
    return bool(found)


async def _add_bot_activity(db: AsyncSession, lead_id: int, content: str) -> None:
    await CrmActivityRepository(db).create(
        {
            "lead_id": lead_id,
            "activity_type": BOT_ACTIVITY_TYPE,
            "content": content,
            "counsellor_id": None,
            "counsellor_name": BOT_ACTOR,
        },
        commit=False,
    )


def _transcript_text(payload: WhatsAppBotLeadSync, header: str) -> str:
    lines = [header]
    if payload.first_contact_at:
        lines.append(f"First message: {_sl_time(payload.first_contact_at)} (Sri Lanka time)")
    lines.append("")
    for item in payload.transcript:
        who = SENDER_LABELS.get(item.sender.lower(), item.sender.title())
        stamp = f"[{_sl_time(item.sent_at)}] " if item.sent_at else ""
        lines.append(f"{stamp}{who}: {item.message.strip()}")
    content = "\n".join(lines)
    if len(content) > MAX_TRANSCRIPT_CHARS:
        content = content[:MAX_TRANSCRIPT_CHARS] + "\n… (transcript truncated; full chat is in the bot dashboard)"
    return content


def _assign(lead: CrmLead, counsellor: CrmCounsellorOut) -> None:
    lead.assigned_counsellor_id = counsellor.user_id
    lead.counsellor_name = counsellor.name
    lead.assigned_at = datetime.now(timezone.utc).replace(tzinfo=None)


async def sync_whatsapp_bot_lead(db: AsyncSession, payload: WhatsAppBotLeadSync) -> dict:
    external_id = _external_id(payload.bot_lead_id)
    mapped = _mapped_fields(payload)
    normalized_phone = service._normalized_lead_phone(payload.phone)

    # Serialize pushes for the same number (the same lock key manual entry uses).
    if len(normalized_phone) >= 7:
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:phone))"), {"phone": normalized_phone})

    counsellor = await _find_counsellor(db, payload)
    bot_counsellor_label = None
    if payload.counsellor:
        bot_counsellor_label = payload.counsellor.name or payload.counsellor.email or "unknown"
        if payload.counsellor.email:
            bot_counsellor_label += f" <{payload.counsellor.email}>"

    lead = await _lead_by_external(db, external_id)
    action = "updated"
    if lead is None:
        lead = await _lead_by_phone(db, payload.phone)
        action = "merged" if lead is not None else "created"

    notes = []

    if action == "created":
        create_payload = CrmLeadCreate(
            full_name=mapped["full_name"] or "WhatsApp Lead",
            email=mapped["email"],
            phone=mapped["phone"],
            whatsapp=mapped["whatsapp"],
            highest_qualification=mapped["highest_qualification"],
            awarding_body=mapped["awarding_body"],
            faculty=mapped["faculty"],
            interested_programme=mapped["interested_programme"],
            interested_course=mapped["interested_course"],
            source="whatsapp",
            stage="new_lead",
            priority="medium",
            notes=_clean(payload.notes, 5000),
            external_record_id=external_id,
        )
        created = await service.create_lead(
            db, create_payload, counsellor_name=BOT_ACTOR, auto_assign=False
        )
        lead = await db.get(CrmLead, created.lead_id, populate_existing=True)
        # create_lead committed, so take the phone lock again for the rest.
        if len(normalized_phone) >= 7:
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:phone))"), {"phone": normalized_phone})
        if counsellor:
            _assign(lead, counsellor)
            notes.append(f"Assigned to {counsellor.name} by the WhatsApp bot handover.")
    else:
        # Fill only empty CRM fields; never overwrite what counsellors entered.
        for key, value in mapped.items():
            if value and not getattr(lead, key):
                setattr(lead, key, value)
        if not lead.external_record_id:
            lead.external_record_id = external_id
        is_bot_lead = lead.external_record_id == external_id and lead.source == "whatsapp"
        if counsellor:
            if lead.assigned_counsellor_id is None:
                _assign(lead, counsellor)
                notes.append(f"Assigned to {counsellor.name} by the WhatsApp bot handover.")
            elif lead.assigned_counsellor_id != counsellor.user_id:
                if is_bot_lead:
                    previous = lead.counsellor_name or f"Counsellor #{lead.assigned_counsellor_id}"
                    _assign(lead, counsellor)
                    notes.append(f"Reassigned from {previous} to {counsellor.name} by the WhatsApp bot.")
                else:
                    notes.append(
                        f"WhatsApp bot handed this student to {counsellor.name}; "
                        f"kept existing counsellor {lead.counsellor_name or lead.assigned_counsellor_id}."
                    )
        if action == "merged":
            notes.insert(0, f"Existing lead matched by phone; WhatsApp bot conversation merged (source kept as {lead.source}).")

    if payload.counsellor and counsellor is None:
        notes.append(
            f"WhatsApp bot assigned {bot_counsellor_label}, but no CRM counsellor matches that email/name. "
            "Add them as a CRM counsellor with the same email, or assign manually."
        )

    # One handover entry per (session, counsellor).
    if payload.bot_session_id:
        handover_prefix = f"WhatsApp bot handover [{payload.bot_session_id}] -> {bot_counsellor_label or 'pending'}"
        if not await _activity_exists(db, lead.lead_id, handover_prefix):
            details = [handover_prefix]
            if payload.handover_reason:
                details.append(f"Reason: {payload.handover_reason}")
            if payload.handed_over_at:
                details.append(f"Handed over: {_sl_time(payload.handed_over_at)} (Sri Lanka time)")
            for label, value in (
                ("Qualification", payload.qualification),
                ("Pathway", payload.pathway),
                ("Field", payload.field),
                ("Course interest", payload.course_interest),
                ("Email", payload.email),
                ("Bot status", payload.bot_status),
            ):
                if value:
                    details.append(f"{label}: {value}")
            details.extend(notes)
            await _add_bot_activity(db, lead.lead_id, "\n".join(details))
        elif notes:
            await _add_bot_activity(db, lead.lead_id, "\n".join(notes))
    elif notes:
        await _add_bot_activity(db, lead.lead_id, "\n".join(notes))

    # Full chat transcript, once per bot session.
    if payload.transcript:
        header = f"WhatsApp bot conversation [{payload.bot_session_id or payload.bot_lead_id}]"
        if not await _activity_exists(db, lead.lead_id, header):
            await _add_bot_activity(db, lead.lead_id, _transcript_text(payload, header))

    await db.execute(text("UPDATE crm_leads SET last_activity_at=now() WHERE lead_id=:id"), {"id": lead.lead_id})
    await db.commit()
    await db.refresh(lead)
    return {
        "status": "success",
        "action": action,
        "lead_id": lead.lead_id,
        "assigned_counsellor_id": lead.assigned_counsellor_id,
        "counsellor_name": lead.counsellor_name,
        "counsellor_matched": counsellor is not None,
    }


async def add_whatsapp_bot_message(db: AsyncSession, payload: WhatsAppBotMessageSync) -> dict:
    """Append a post-handover WhatsApp message to the lead's timeline."""
    lead = None
    if payload.bot_lead_id:
        lead = await _lead_by_external(db, _external_id(payload.bot_lead_id))
    if lead is None:
        lead = await _lead_by_phone(db, payload.phone)
    if lead is None:
        # Not handed over yet, so not in the CRM by design.
        return {"status": "ignored", "reason": "lead_not_in_crm"}
    who = SENDER_LABELS.get(payload.sender.lower(), payload.sender.title())
    if who == "Counsellor" and payload.counsellor_name:
        who = f"Counsellor ({payload.counsellor_name}, via bot dashboard)"
    stamp = f" at {_sl_time(payload.sent_at)}" if payload.sent_at else ""
    content = f"WhatsApp message{stamp}\n{who}: {payload.message.strip()[:MAX_TRANSCRIPT_CHARS]}"
    await _add_bot_activity(db, lead.lead_id, content)
    await db.execute(text("UPDATE crm_leads SET last_activity_at=now() WHERE lead_id=:id"), {"id": lead.lead_id})
    await db.commit()
    return {"status": "success", "lead_id": lead.lead_id}
