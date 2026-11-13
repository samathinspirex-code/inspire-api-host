"""Who gets leads from a given source, set by admins: Meta lead forms and WhatsApp bot handovers.

Kept in its own raw-SQL table, so CRM pages work even before the migration has run.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError

META_GROUP = "meta"
# WhatsApp bot imports pick from this group themselves (app.modules.whatsapp_bot.sync);
# it is deliberately not in SOURCE_GROUPS, so leads typed in with source "whatsapp" are unaffected.
WHATSAPP_BOT_GROUP = "whatsapp_bot"
GROUPS = {META_GROUP, WHATSAPP_BOT_GROUP}
SOURCE_GROUPS = {"meta_lead_forms": META_GROUP}


async def ready(db: AsyncSession) -> bool:
    return bool(await db.scalar(text("SELECT to_regclass('public.crm_source_assignment') IS NOT NULL")))


async def group_counsellor_ids(db: AsyncSession, group: str) -> list[int]:
    if not await ready(db):
        return []
    ids = await db.scalar(text("SELECT counsellor_ids FROM crm_source_assignment WHERE source_group = :group"), {"group": group})
    return list(ids or [])


async def meta_counsellor_ids(db: AsyncSession) -> list[int]:
    return await group_counsellor_ids(db, META_GROUP)


async def set_meta_counsellor_ids(db: AsyncSession, ids: list[int], known_ids: set[int]) -> None:
    await set_group_counsellor_ids(db, META_GROUP, ids, known_ids)


async def set_group_counsellor_ids(db: AsyncSession, group: str, ids: list[int], known_ids: set[int]) -> None:
    if group not in GROUPS:
        raise ValidationError("Unknown lead source group")
    if not await ready(db):
        raise ValidationError("The lead assignment setting needs a database update first. Ask your developer to run it.")
    unknown = [item for item in ids if item not in known_ids]
    if unknown:
        raise ValidationError("Choose only registered counsellors")
    ordered = list(dict.fromkeys(ids))
    await db.execute(text("""
        INSERT INTO crm_source_assignment (source_group, counsellor_ids) VALUES (:group, :ids)
        ON CONFLICT (source_group) DO UPDATE SET counsellor_ids = EXCLUDED.counsellor_ids, updated_at = now()
    """), {"group": group, "ids": ordered})


async def counsellors_including_paused(db: AsyncSession):
    """Everyone with counsellor access, whether or not they are paused in the round robin."""
    from app.modules.crm.schemas import CrmCounsellorOut
    from app.modules.crm.service import _counsellor_users_stmt, _dedupe_counsellor_rows
    rows = _dedupe_counsellor_rows((await db.execute(_counsellor_users_stmt(True))).all())
    return [CrmCounsellorOut(user_id=user.user_id, name=user.full_name or user.email, email=user.email) for user, _status in rows]


async def pick_for_group(db: AsyncSession, group: str, commit_turn: bool = True):
    """Next of the group's chosen counsellors, taking turns. None if nobody is set.

    Meta leads skip counsellors paused in the rotation. WhatsApp bot leads do not:
    ticking someone for them is an explicit choice, so a counsellor paused in the
    round robin still gets them (as long as they keep counsellor access).
    With commit_turn=False it only previews the pick and leaves the turn pointer alone.
    """
    if group not in GROUPS or not await ready(db):
        return None
    row = (await db.execute(text(
        "SELECT counsellor_ids, last_counsellor_id, last_assigned_on FROM crm_source_assignment WHERE source_group = :group FOR UPDATE"
    ), {"group": group})).mappings().first()
    if not row or not row["counsellor_ids"]:
        return None
    from app.modules.crm.service import _next_in_rotation, list_counsellors
    chosen = row["counsellor_ids"]
    if group == WHATSAPP_BOT_GROUP:
        people = {person.user_id: person for person in await counsellors_including_paused(db)}
        queue = [people[user_id] for user_id in chosen if user_id in people]
    else:
        queue = [person for person in await list_counsellors(db) if person.user_id in set(chosen)]
    today = datetime.now(timezone.utc).date()
    picked = _next_in_rotation(queue, row["last_counsellor_id"], row["last_assigned_on"], today)
    if picked is not None and commit_turn:
        await db.execute(text("UPDATE crm_source_assignment SET last_counsellor_id = :cid, last_assigned_on = :today WHERE source_group = :group"),
                         {"cid": picked.user_id, "today": today, "group": group})
    return picked


async def assign_by_source(db: AsyncSession, lead_id: int, source: Optional[str]):
    """Assigns from the source's chosen counsellors who are still active. None means use the normal rotation."""
    group = SOURCE_GROUPS.get(source or "")
    if not group:
        return None
    picked = await pick_for_group(db, group)
    if picked is None:
        return None
    await db.execute(text("UPDATE crm_leads SET assigned_counsellor_id = :cid, counsellor_name = :name, assigned_at = now() WHERE lead_id = :lead_id"),
                     {"cid": picked.user_id, "name": picked.name, "lead_id": lead_id})
    return picked
