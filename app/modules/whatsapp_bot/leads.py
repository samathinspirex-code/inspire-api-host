"""Mirror one WhatsApp bot lead into the CRM (create or update), never writing to the bot.

Field rule: a CRM field is filled from the bot when it is empty, or when it still
holds the value the bot gave last time (so the bot can correct itself, e.g. a
placeholder name replaced by the real one). A value a counsellor typed in the CRM
is never overwritten.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.crm import service
from app.modules.crm.models.lead import CrmLead
from app.modules.crm.schemas import CrmCounsellorOut, CrmLeadCreate, WhatsAppBotLeadSync
from app.modules.crm.whatsapp_bot_service import (
    BOT_ACTOR,
    _add_bot_activity,
    _assign,
    _external_id,
    _lead_by_external,
    _lead_by_phone,
    _mapped_fields,
)

PLACEHOLDER_NAMES = {"", "whatsapp lead", "whatsapp prospect"}
LABELS = {
    "full_name": "Name", "email": "Email", "phone": "Phone", "whatsapp": "WhatsApp",
    "highest_qualification": "Qualification", "awarding_body": "Awarding body", "faculty": "Field",
    "interested_programme": "Programme", "interested_course": "Course",
}


def bot_values(bot_lead: dict) -> dict:
    """Bot lead row -> CRM column values (only what the bot actually has)."""
    payload = WhatsAppBotLeadSync(
        bot_lead_id=bot_lead["id"],
        phone=bot_lead["phone"],
        full_name=(bot_lead.get("name") or None) and str(bot_lead["name"])[:255],
        email=(bot_lead.get("email") or None) and str(bot_lead["email"])[:255],
        qualification=(bot_lead.get("qualification") or None) and str(bot_lead["qualification"])[:100],
        pathway=(bot_lead.get("pathway") or None) and str(bot_lead["pathway"])[:100],
        field=(bot_lead.get("field") or None) and str(bot_lead["field"])[:100],
        course_interest=(bot_lead.get("course_interest") or None) and str(bot_lead["course_interest"])[:255],
    )
    return {key: value for key, value in _mapped_fields(payload).items() if value}


def _empty(key: str, value) -> bool:
    if value is None or value == "":
        return True
    return key == "full_name" and str(value).strip().lower() in PLACEHOLDER_NAMES


def plan_updates(current: dict, values: dict, previous: Optional[dict]) -> dict:
    """Which CRM fields to set from the bot: empty ones, or ones still holding the bot's last value."""
    old = previous or {}
    return {
        key: value for key, value in values.items()
        if current.get(key) != value and (_empty(key, current.get(key)) or (key in old and current.get(key) == old[key]))
    }


def _sl(value: Optional[datetime]) -> Optional[datetime]:
    """Bot timestamps (UTC, aware) -> naive UTC, as CRM columns store them."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


async def mirror_lead(db: AsyncSession, bot_lead: dict, previous: Optional[dict],
                      assignee: Optional[CrmCounsellorOut], created_at: Optional[datetime]) -> dict:
    """Create or update the CRM lead for this bot lead. Returns {action, lead_id, changed, values}."""
    values = bot_values(bot_lead)
    external_id = _external_id(bot_lead["id"])
    phone_key = service._normalized_lead_phone(bot_lead["phone"])
    if len(phone_key) >= 7:
        # Same lock manual entry and the old bot push used, so two writers can't create a duplicate.
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:phone))"), {"phone": phone_key})

    lead = await _lead_by_external(db, external_id)
    action = "updated"
    if lead is None:
        lead = await _lead_by_phone(db, bot_lead["phone"])
        action = "merged" if lead is not None else "created"

    if action == "created":
        created = await service.create_lead(db, CrmLeadCreate(
            full_name=values.get("full_name") or "WhatsApp Lead",
            created_at=_sl(created_at),
            email=values.get("email"),
            phone=values.get("phone"),
            whatsapp=values.get("whatsapp"),
            highest_qualification=values.get("highest_qualification"),
            awarding_body=values.get("awarding_body"),
            faculty=values.get("faculty"),
            interested_programme=values.get("interested_programme"),
            interested_course=values.get("interested_course"),
            source="whatsapp",
            stage="new_lead",
            priority="medium",
            external_record_id=external_id,
        ), counsellor_name=BOT_ACTOR, auto_assign=False)
        lead = await db.get(CrmLead, created.lead_id, populate_existing=True)
        if len(phone_key) >= 7:  # create_lead committed; lock again for the rest.
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:phone))"), {"phone": phone_key})
        lines = ["WhatsApp bot lead imported"]
        lines += [f"{LABELS.get(k, k)}: {v}" for k, v in values.items() if k not in ("phone", "whatsapp")]
        if assignee:
            _assign(lead, assignee)
            lines.append(f"Assigned to {assignee.name} (WhatsApp bot lead setting).")
        lines.append("Full chat: CRM → WhatsApp Bot → Chat History.")
        await _add_bot_activity(db, lead.lead_id, "\n".join(lines))
        changed = list(values)
    else:
        updates = plan_updates({key: getattr(lead, key, None) for key in values}, values, previous)
        for key, value in updates.items():
            setattr(lead, key, value)
        changed = list(updates)
        notes = []
        if not lead.external_record_id:
            lead.external_record_id = external_id
            notes.append(f"Matched an existing lead by phone; WhatsApp bot details are now linked (source kept as {lead.source}).")
        if lead.assigned_counsellor_id is None and assignee:
            _assign(lead, assignee)
            notes.append(f"Assigned to {assignee.name} (WhatsApp bot lead setting).")
        if changed or notes:
            lines = ["WhatsApp bot updated this lead"] + notes
            lines += [f"{LABELS.get(k, k)}: {values[k]}" for k in changed if k not in ("phone", "whatsapp")]
            await _add_bot_activity(db, lead.lead_id, "\n".join(lines))

    if changed or action != "updated":
        await db.execute(text("UPDATE crm_leads SET last_activity_at = now() WHERE lead_id = :id"), {"id": lead.lead_id})
    await db.commit()
    return {"action": action, "lead_id": lead.lead_id, "changed": changed, "values": values}
