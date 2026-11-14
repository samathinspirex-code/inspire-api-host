"""Mirror WhatsApp bot leads into the CRM in near real time (read-only on the bot).

Rules agreed 14 November 2026:

* A CRM lead is created as soon as the bot has a lead with a real phone number;
  the bot's handovers are not the trigger any more.
* When the student gives the bot more details (name, qualification, pathway,
  field, course, email), the CRM lead is filled in on the next pass. A value a
  counsellor typed in the CRM is never overwritten (see leads.py).
* No duplicates: a bot lead is matched by its bot id (external_record_id
  "wabot:<id>"), then by phone, so numbers already in the CRM are updated.
* New leads go to the counsellors ticked under CRM -> Counsellors ->
  "WhatsApp bot leads go to" (taking turns), else WABOT_ASSIGN_TO_EMAIL.
* Nothing is ever written to the bot's database.

Only bot leads created on or after the start point are mirrored. The start
point is set once (first run, or `--since` on the script) and kept in
wabot_sync_meta, together with the cursor: the latest bot `updated_at` seen.

Runs continuously from `scripts/run_wabot_sync.py --watch` (every 20 seconds),
or on demand from POST /api/v1/cms/whatsapp-bot/sync.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.crm.schemas import CrmCounsellorOut
from app.modules.crm.source_assignment import WHATSAPP_BOT_GROUP, counsellors_including_paused, pick_for_group
from app.modules.whatsapp_bot.leads import bot_values, mirror_lead
from app.modules.whatsapp_bot.supabase import BotDB, BotUnavailable, configured, in_list

log = logging.getLogger(__name__)

PAGE = 200
MAX_PAGES = 10  # Up to 2000 changed leads per pass; the next pass carries on.
LEAD_COLUMNS = "id,phone,name,email,qualification,pathway,field,course_interest,status,source,created_at,updated_at"


def parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    value = str(value).replace("Z", "+00:00")
    value = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"), value)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_phone(value: Optional[str]) -> bool:
    return len(re.sub(r"\D", "", value or "")) >= 7


# ---------------------------------------------------------------------------
# CRM-side state (main database only)
# ---------------------------------------------------------------------------
async def _meta(db: AsyncSession, key: str) -> Optional[dict]:
    try:
        return await db.scalar(text("SELECT value FROM wabot_sync_meta WHERE key = :k"), {"k": key})
    except Exception as exc:
        await db.rollback()
        raise RuntimeError("Run scripts/apply_whatsapp_bot_migration.py first") from exc


async def _set_meta(db: AsyncSession, key: str, value: dict) -> None:
    await db.execute(text(
        "INSERT INTO wabot_sync_meta (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
    ), {"k": key, "v": json.dumps(value, default=str)})
    await db.commit()


async def lead_state(db: AsyncSession, bot_lead_ids: list[str]) -> dict:
    if not bot_lead_ids:
        return {}
    try:
        rows = (await db.execute(text(
            "SELECT bot_lead_id, crm_lead_id, bot_values, synced_at FROM wabot_lead_state WHERE bot_lead_id = ANY(:ids)"
        ), {"ids": bot_lead_ids})).mappings().all()
    except Exception:  # Not migrated yet.
        await db.rollback()
        return {}
    return {r["bot_lead_id"]: dict(r) for r in rows}


async def _save_lead_state(db: AsyncSession, bot_lead_id: str, crm_lead_id: int, values: dict, bot_updated_at) -> None:
    await db.execute(text(
        "INSERT INTO wabot_lead_state (bot_lead_id, crm_lead_id, bot_values, bot_updated_at, synced_at) "
        "VALUES (:id, :lid, CAST(:v AS jsonb), :u, now()) "
        "ON CONFLICT (bot_lead_id) DO UPDATE SET crm_lead_id = EXCLUDED.crm_lead_id, bot_values = EXCLUDED.bot_values, "
        "bot_updated_at = EXCLUDED.bot_updated_at, synced_at = now()"
    ), {"id": bot_lead_id, "lid": crm_lead_id, "v": json.dumps(values), "u": bot_updated_at})
    await db.commit()


async def sync_status(db: AsyncSession) -> dict:
    """Counts and last run for the Connectors page and /status."""
    try:
        row = (await db.execute(text(
            "SELECT (SELECT count(*) FROM wabot_lead_state) AS synced_leads, "
            "(SELECT updated_at FROM wabot_sync_meta WHERE key = 'last_run') AS last_sync_at, "
            "(SELECT value FROM wabot_sync_meta WHERE key = 'last_run') AS last_run, "
            "(SELECT value FROM wabot_sync_meta WHERE key = 'leads_cursor') AS cursor"
        ))).mappings().one()
    except Exception:  # Tables not migrated yet.
        await db.rollback()
        return {"synced_sessions": 0, "synced_leads": 0, "last_sync_at": None, "last_run": None, "since": None, "migrated": False}
    cursor = row["cursor"] or {}
    return {
        "synced_sessions": row["synced_leads"],  # kept for the Connectors page
        "synced_leads": row["synced_leads"],
        "last_sync_at": row["last_sync_at"].isoformat() if row["last_sync_at"] else None,
        "last_run": row["last_run"],
        "since": cursor.get("since"),
        "migrated": True,
    }


# ---------------------------------------------------------------------------
# Who gets new leads
# ---------------------------------------------------------------------------
async def resolve_assignee(db: AsyncSession, preview: bool = False) -> Optional[CrmCounsellorOut]:
    picked = await pick_for_group(db, WHATSAPP_BOT_GROUP, commit_turn=not preview)
    if picked is not None:
        return picked
    email = (settings.WABOT_ASSIGN_TO_EMAIL or "").strip().lower()
    if not email:
        return None
    return next((p for p in await counsellors_including_paused(db) if (p.email or "").lower() == email), None)


async def pick_assignee(db: AsyncSession, preview: bool = False) -> Optional[dict]:
    """Display form for /status: who the next new lead goes to."""
    person = await resolve_assignee(db, preview=preview)
    return {"email": person.email, "name": person.name} if person else None


# ---------------------------------------------------------------------------
# The mirror pass
# ---------------------------------------------------------------------------
async def run_sync(db: AsyncSession, bot: Optional[BotDB] = None, dry_run: bool = False,
                   since: Optional[datetime] = None) -> dict:
    """One pass: mirror bot leads changed since the cursor. Never writes to the bot.

    `since` (re)sets the start point and rescans from it, e.g. to backfill a day.
    """
    summary: dict = {"created": 0, "merged": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": []}
    if dry_run:
        summary["would_import"] = []
    if not configured():
        summary["errors"].append("WhatsApp bot database is not configured")
        return summary
    owned = bot is None
    bot = bot or BotDB()
    if owned:
        await bot.__aenter__()
    try:
        cursor = await _meta(db, "leads_cursor") or {}
        now = datetime.now(timezone.utc)
        if since is not None:
            start, after = since, since
        elif cursor.get("since"):
            start, after = parse_ts(cursor["since"]), parse_ts(cursor.get("updated_at")) or parse_ts(cursor["since"])
        else:
            start = after = now  # First run with no start point: only leads from now on.
        newest = after
        for _ in range(MAX_PAGES):
            rows = await bot.select("leads", {
                "select": LEAD_COLUMNS,
                "and": f"(updated_at.gte.{after.isoformat()},created_at.gte.{start.isoformat()})",
                "order": "updated_at.asc,id.asc", "limit": str(PAGE),
            })
            if not rows:
                break
            state = await lead_state(db, [r["id"] for r in rows])
            for row in rows:
                stamp = parse_ts(row.get("updated_at"))
                if not is_phone(row.get("phone")):
                    summary["skipped"] += 1  # website chats use web_ ids, not phone numbers
                    newest = max(newest, stamp or newest)
                    continue
                saved = state.get(row["id"])
                values = bot_values(row)
                if saved and saved["bot_values"] == values:
                    summary["unchanged"] += 1
                    newest = max(newest, stamp or newest)
                    continue
                if dry_run:
                    summary["would_import"].append({"name": row.get("name"), "phone": row["phone"],
                                                    "created_at": row.get("created_at"), "new": saved is None})
                    continue
                try:
                    assignee = await resolve_assignee(db)
                    result = await mirror_lead(db, row, saved["bot_values"] if saved else None, assignee, parse_ts(row.get("created_at")))
                    await _save_lead_state(db, row["id"], result["lead_id"], values, stamp)
                    summary[result["action"]] += 1
                    newest = max(newest, stamp or newest)
                except Exception as exc:  # One bad lead must not stop the rest; it is retried next pass.
                    await db.rollback()
                    log.exception("WhatsApp bot lead mirror failed for %s", row.get("id"))
                    summary["errors"].append(f"{row.get('phone')}: {exc.__class__.__name__}: {str(exc)[:200]}")
            if len(rows) < PAGE:
                break
            after = parse_ts(rows[-1]["updated_at"])
        if not dry_run:
            # After a failure keep the old position, so the failed lead is retried next pass
            # (leads already mirrored are skipped cheaply as "unchanged").
            kept = newest if not summary["errors"] else (parse_ts(cursor.get("updated_at")) if since is None and cursor.get("updated_at") else start)
            await _set_meta(db, "leads_cursor", {"since": start.isoformat(), "updated_at": kept.isoformat()})
    except BotUnavailable as exc:
        summary["errors"].append(exc.message)
    except RuntimeError as exc:
        summary["errors"].append(str(exc))
    finally:
        if owned:
            await bot.__aexit__(None, None, None)
    if not dry_run:
        try:
            await _set_meta(db, "last_run", summary)
        except Exception:
            await db.rollback()
    return summary


async def imported_by_bot_lead(db: AsyncSession, bot_lead_ids: list[str]) -> dict:
    """bot lead id -> CRM lead id, for the screens."""
    return {k: v["crm_lead_id"] for k, v in (await lead_state(db, bot_lead_ids)).items()}
