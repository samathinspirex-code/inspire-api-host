"""Who gets leads from a given source, set by admins. Meta lead forms only, for now.

Kept in its own raw-SQL table, so CRM pages work even before the migration has run.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError

META_GROUP = "meta"
SOURCE_GROUPS = {"meta_lead_forms": META_GROUP}


async def ready(db: AsyncSession) -> bool:
    return bool(await db.scalar(text("SELECT to_regclass('public.crm_source_assignment') IS NOT NULL")))


async def meta_counsellor_ids(db: AsyncSession) -> list[int]:
    if not await ready(db):
        return []
    ids = await db.scalar(text("SELECT counsellor_ids FROM crm_source_assignment WHERE source_group = :group"), {"group": META_GROUP})
    return list(ids or [])


async def set_meta_counsellor_ids(db: AsyncSession, ids: list[int], known_ids: set[int]) -> None:
    if not await ready(db):
        raise ValidationError("The Meta assignment setting needs a database update first. Ask your developer to run it.")
    unknown = [item for item in ids if item not in known_ids]
    if unknown:
        raise ValidationError("Choose only registered counsellors")
    ordered = list(dict.fromkeys(ids))
    await db.execute(text("""
        INSERT INTO crm_source_assignment (source_group, counsellor_ids) VALUES (:group, :ids)
        ON CONFLICT (source_group) DO UPDATE SET counsellor_ids = EXCLUDED.counsellor_ids, updated_at = now()
    """), {"group": META_GROUP, "ids": ordered})


async def assign_by_source(db: AsyncSession, lead_id: int, source: Optional[str]):
    """Assigns from the source's chosen counsellors who are still active. None means use the normal rotation."""
    group = SOURCE_GROUPS.get(source or "")
    if not group or not await ready(db):
        return None
    row = (await db.execute(text(
        "SELECT counsellor_ids, last_counsellor_id, last_assigned_on FROM crm_source_assignment WHERE source_group = :group FOR UPDATE"
    ), {"group": group})).mappings().first()
    if not row or not row["counsellor_ids"]:
        return None
    from app.modules.crm.service import _next_in_rotation, list_counsellors
    chosen = set(row["counsellor_ids"])
    queue = [person for person in await list_counsellors(db) if person.user_id in chosen]
    today = datetime.now(timezone.utc).date()
    picked = _next_in_rotation(queue, row["last_counsellor_id"], row["last_assigned_on"], today)
    if picked is None:
        return None
    await db.execute(text("UPDATE crm_source_assignment SET last_counsellor_id = :cid, last_assigned_on = :today WHERE source_group = :group"),
                     {"cid": picked.user_id, "today": today, "group": group})
    await db.execute(text("UPDATE crm_leads SET assigned_counsellor_id = :cid, counsellor_name = :name, assigned_at = now() WHERE lead_id = :lead_id"),
                     {"cid": picked.user_id, "name": picked.name, "lead_id": lead_id})
    return picked
