"""Copy pending WhatsApp bot handovers into CRM leads (read-only on the bot).

Replaces the bot-side push (bot/insbot-1/lib/crm.js -> /integrations/crm/whatsapp-bot/*).
Rules agreed 13 November 2026:

* Only handovers that are **pending** in the bot (the "Pending Handovers" list)
  are imported. Older, already-accepted handovers are not backfilled.
* Every import is assigned to the counsellors ticked under CRM -> Counsellors ->
  "WhatsApp bot leads go to" (taking turns), changeable at any time. If none is
  ticked, WABOT_ASSIGN_TO_EMAIL is used. The bot's own pick is ignored.
* Nothing is ever written to the bot's database. Its handover stays pending
  there; the CRM keeps its own record in wabot_sync_state.
* After import, new WhatsApp messages in that chat are copied onto the CRM
  lead timeline.

Lead matching, merge-by-phone and timeline entries live in
app.modules.crm.whatsapp_bot_service and are unchanged.

Runs from scripts/run_wabot_sync.py (timer, every 5 minutes) and from
POST /api/v1/cms/whatsapp-bot/sync.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.crm.schemas import WhatsAppBotCounsellor, WhatsAppBotLeadSync, WhatsAppBotMessageSync
from app.modules.crm.source_assignment import WHATSAPP_BOT_GROUP, pick_for_group
from app.modules.crm.whatsapp_bot_service import add_whatsapp_bot_message, sync_whatsapp_bot_lead
from app.modules.whatsapp_bot.supabase import BotDB, BotUnavailable, configured, in_list

log = logging.getLogger(__name__)

MAX_TRANSCRIPT_MESSAGES = 1000
FOLLOW_IMPORTED_SESSIONS = 300  # Recently imported chats whose new messages are still copied.


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
async def imported(db: AsyncSession, session_ids: Optional[list[str]] = None) -> dict:
    """bot session id -> import record. With no ids, the most recently imported chats."""
    if session_ids is not None and not session_ids:
        return {}
    sql = ("SELECT bot_session_id, bot_handover_id, crm_lead_id, last_message_at, handover_synced_at "
           "FROM wabot_sync_state ")
    try:
        if session_ids is None:
            rows = (await db.execute(text(sql + "ORDER BY updated_at DESC LIMIT :n"), {"n": FOLLOW_IMPORTED_SESSIONS})).mappings().all()
        else:
            rows = (await db.execute(text(sql + "WHERE bot_session_id = ANY(:ids)"), {"ids": session_ids})).mappings().all()
    except Exception as exc:
        await db.rollback()
        if "wabot_sync_state" in str(exc):
            raise RuntimeError("Run scripts/apply_whatsapp_bot_migration.py first") from exc
        raise
    return {r["bot_session_id"]: dict(r) for r in rows}


async def imported_by_handover(db: AsyncSession, handover_ids: list[str]) -> dict:
    if not handover_ids:
        return {}
    try:
        rows = (await db.execute(text(
            "SELECT bot_handover_id, crm_lead_id, handover_synced_at FROM wabot_sync_state WHERE bot_handover_id = ANY(:ids)"
        ), {"ids": handover_ids})).mappings().all()
    except Exception:  # Not migrated yet.
        await db.rollback()
        return {}
    return {r["bot_handover_id"]: dict(r) for r in rows}


async def _save_state(db: AsyncSession, session_id: str, handover_id: Optional[str], lead_id: Optional[int],
                      last_message_at: Optional[datetime]) -> None:
    await db.execute(text(
        "INSERT INTO wabot_sync_state (bot_session_id, bot_handover_id, crm_lead_id, handover_synced_at, last_message_at, updated_at) "
        "VALUES (:sid, :hid, :lid, now(), :lm, now()) "
        "ON CONFLICT (bot_session_id) DO UPDATE SET "
        "bot_handover_id = COALESCE(wabot_sync_state.bot_handover_id, EXCLUDED.bot_handover_id), "
        "crm_lead_id = COALESCE(EXCLUDED.crm_lead_id, wabot_sync_state.crm_lead_id), "
        "last_message_at = GREATEST(COALESCE(EXCLUDED.last_message_at, wabot_sync_state.last_message_at), wabot_sync_state.last_message_at), "
        "updated_at = now()"
    ), {"sid": session_id, "hid": handover_id, "lid": lead_id, "lm": last_message_at})
    await db.commit()


async def _save_run(db: AsyncSession, summary: dict) -> None:
    await db.execute(text(
        "INSERT INTO wabot_sync_meta (key, value, updated_at) VALUES ('last_run', CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
    ), {"v": json.dumps(summary, default=str)})
    await db.commit()


async def sync_status(db: AsyncSession) -> dict:
    """Counts and last run for the Connectors page and /status."""
    try:
        row = (await db.execute(text(
            "SELECT (SELECT count(*) FROM wabot_sync_state) AS synced_sessions, "
            "(SELECT updated_at FROM wabot_sync_meta WHERE key = 'last_run') AS last_sync_at, "
            "(SELECT value FROM wabot_sync_meta WHERE key = 'last_run') AS last_run"
        ))).mappings().one()
    except Exception:  # Table not migrated yet.
        await db.rollback()
        return {"synced_sessions": 0, "last_sync_at": None, "last_run": None, "migrated": False}
    return {
        "synced_sessions": row["synced_sessions"],
        "last_sync_at": row["last_sync_at"].isoformat() if row["last_sync_at"] else None,
        "last_run": row["last_run"],
        "migrated": True,
    }


# ---------------------------------------------------------------------------
# Building the CRM payload (reads only)
# ---------------------------------------------------------------------------
async def _transcript(bot: BotDB, phone: str, session_id: str) -> list[dict]:
    """The current chat plus earlier chats for this number that were never handed over."""
    sessions = await bot.select("chat_sessions", {"select": "id", "phone": f"eq.{phone}"})
    ids = [s["id"] for s in sessions] or [session_id]
    others = await bot.select("handovers", {"select": "chat_session_id", "chat_session_id": in_list(ids)}) if len(ids) > 1 else []
    handed = {h["chat_session_id"] for h in others if h["chat_session_id"] != session_id}
    included = [i for i in ids if i == session_id or i not in handed]
    messages = await bot.select("chat_messages", {
        "select": "sender,message,created_at", "session_id": in_list(included),
        "order": "created_at.asc", "limit": str(MAX_TRANSCRIPT_MESSAGES),
    })
    return [m for m in messages if m.get("message")]


async def pick_assignee(db: AsyncSession, preview: bool = False) -> Optional[dict]:
    """The CRM counsellor for the next import: the admin setting first, then the env fallback."""
    picked = await pick_for_group(db, WHATSAPP_BOT_GROUP, commit_turn=not preview)
    if picked is not None:
        return {"email": picked.email, "name": picked.name, "phone": None}
    email = (settings.WABOT_ASSIGN_TO_EMAIL or "").strip().lower()
    return {"email": email, "name": None, "phone": None} if email else None


async def build_payload(bot: BotDB, session: dict, handover: dict) -> Optional[WhatsAppBotLeadSync]:
    lead = None
    for lead_id in (handover.get("lead_id"), session.get("lead_id")):
        if lead_id and not lead:
            lead = await bot.one("leads", {"id": f"eq.{lead_id}"})
    if not lead:
        lead = await bot.one("leads", {"phone": f"eq.{session['phone']}"})
    if not lead or not is_phone(lead.get("phone")):
        return None  # Website chat "phones" are web_ session ids.
    transcript = await _transcript(bot, session["phone"], session["id"])
    return WhatsAppBotLeadSync(
        bot_lead_id=lead["id"],
        bot_session_id=session["id"],
        phone=lead["phone"],
        full_name=lead.get("name"),
        email=lead.get("email"),
        qualification=lead.get("qualification"),
        pathway=lead.get("pathway"),
        field=lead.get("field"),
        course_interest=lead.get("course_interest"),
        bot_status=lead.get("status"),
        notes=(lead.get("notes") or None) and lead["notes"][:5000],
        first_contact_at=parse_ts(lead.get("created_at")),
        handover_reason=(handover.get("reason") or None) and handover["reason"][:500],
        handed_over_at=parse_ts(handover.get("assigned_at")),
        counsellor=None,
        transcript=[{"sender": m["sender"], "message": str(m["message"])[:10000], "sent_at": parse_ts(m["created_at"])}
                    for m in transcript],
    )


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------
async def pending_handovers(bot: BotDB, only_sessions: Optional[list[str]] = None) -> list[dict]:
    params = {
        "select": "id,chat_session_id,lead_id,reason,status,assigned_at",
        "status": "eq.pending", "chat_session_id": "not.is.null",
        "order": "assigned_at.asc", "limit": "500",
    }
    if only_sessions:
        params["chat_session_id"] = in_list(only_sessions)
    return await bot.select("handovers", params)


async def _copy_new_messages(db: AsyncSession, bot: BotDB, session: dict, since: datetime) -> tuple[int, Optional[datetime]]:
    messages = await bot.select("chat_messages", {
        "select": "sender,message,created_at", "session_id": f"eq.{session['id']}",
        "created_at": f"gt.{since.isoformat()}", "order": "created_at.asc", "limit": "500",
    })
    newest, copied = None, 0
    for m in messages:
        stamp = parse_ts(m["created_at"])
        if m.get("message"):
            result = await add_whatsapp_bot_message(db, WhatsAppBotMessageSync(
                phone=session["phone"], sender=m["sender"], message=str(m["message"])[:10000], sent_at=stamp,
            ))
            copied += result.get("status") == "success"
        newest = stamp
    return copied, newest


async def run_sync(db: AsyncSession, bot: Optional[BotDB] = None, dry_run: bool = False) -> dict:
    """Import new pending handovers and copy new messages of imported chats. Never writes to the bot."""
    summary: dict = {"imported": 0, "messages": 0, "skipped": 0, "already_imported": 0, "errors": [], "would_import": []}
    if not configured():
        summary["errors"].append("WhatsApp bot database is not configured")
        return summary
    owned = bot is None
    bot = bot or BotDB()
    if owned:
        await bot.__aenter__()
    try:
        pending = await pending_handovers(bot)
        session_ids = list({h["chat_session_id"] for h in pending})
        try:
            done = await imported(db, session_ids)
        except RuntimeError:
            if not dry_run:
                raise
            done = {}  # Preview before the migration: nothing imported yet.
        following = {} if dry_run else await imported(db)
        wanted = set(session_ids) | set(following)
        sessions = {s["id"]: s for s in await bot.select("chat_sessions", {"id": in_list(wanted)})} if wanted else {}

        for handover in pending:
            sid = handover["chat_session_id"]
            session = sessions.get(sid)
            if sid in done:
                summary["already_imported"] += 1
                continue
            if not session:
                summary["skipped"] += 1
                continue
            try:
                payload = await build_payload(bot, session, handover)
                if payload is None:
                    summary["skipped"] += 1
                    continue
                assignee = await pick_assignee(db, preview=dry_run)
                payload = payload.model_copy(update={"counsellor": WhatsAppBotCounsellor(**assignee) if assignee else None})
                if dry_run:
                    summary["would_import"].append({"name": payload.full_name, "phone": payload.phone,
                                                    "handed_over_at": payload.handed_over_at, "messages": len(payload.transcript),
                                                    "assign_to": (assignee or {}).get("name") or (assignee or {}).get("email")})
                    continue
                result = await sync_whatsapp_bot_lead(db, payload)
                newest = max((m.sent_at for m in payload.transcript if m.sent_at), default=None)
                await _save_state(db, sid, handover["id"], result.get("lead_id"), newest)
                done[sid] = {"crm_lead_id": result.get("lead_id")}
                summary["imported"] += 1
                if not result.get("counsellor_matched"):
                    who = payload.counsellor.email if payload.counsellor else "nobody (tick counsellors under CRM -> Counsellors)"
                    summary["errors"].append(f"{payload.phone}: imported unassigned, no CRM counsellor for {who}")
            except BotUnavailable:
                raise
            except Exception as exc:  # One bad chat must not stop the rest.
                await db.rollback()
                log.exception("WhatsApp bot import failed for session %s", sid)
                summary["errors"].append(f"{sid}: {exc.__class__.__name__}: {str(exc)[:200]}")

        for sid, record in following.items():
            session = sessions.get(sid)
            since = record["last_message_at"] or record["handover_synced_at"]
            if not session or not since:
                continue
            try:
                copied, newest = await _copy_new_messages(db, bot, session, since)
                if newest:
                    await _save_state(db, sid, None, None, newest)
                summary["messages"] += copied
            except BotUnavailable:
                raise
            except Exception as exc:
                await db.rollback()
                log.exception("WhatsApp bot message copy failed for session %s", sid)
                summary["errors"].append(f"{sid}: {exc.__class__.__name__}: {str(exc)[:200]}")
    except BotUnavailable as exc:
        summary["errors"].append(exc.message)
    except RuntimeError as exc:
        summary["errors"].append(str(exc))
    finally:
        if owned:
            await bot.__aexit__(None, None, None)
    if not dry_run:
        summary.pop("would_import")
        try:
            await _save_run(db, summary)
        except Exception:
            await db.rollback()
    return summary
