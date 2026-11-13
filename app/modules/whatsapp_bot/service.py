"""CRM -> WhatsApp Bot admin data.

Read-only views of the bot's own admin pages (bot/insbot-1/pages/admin/*),
run server-side with the service key instead of the browser anon key. Nothing
here writes to the bot. Rows are returned as the bot stores them.
"""

import csv
import io
import re
from datetime import datetime, timezone
from typing import Optional

from app.core.errors import NotFoundError, ValidationError
from app.modules.whatsapp_bot.supabase import BotDB, in_list

SESSION_STATUSES = {"active", "handed_over", "closed"}
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_id(value: str, label: str = "id") -> str:
    if not UUID_RE.match(value or ""):
        raise ValidationError(f"Invalid {label}")
    return value


async def dashboard(bot: BotDB) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    stats = {
        "total_leads": await bot.count("leads"),
        "leads_today": await bot.count("leads", {"created_at": f"gte.{today}"}),
        "active_chats": await bot.count("chat_sessions", {"status": "eq.active"}),
        "pending_handovers": await bot.count("handovers", {"status": "eq.pending"}),
        "available_counselors": await bot.count("counselors", {"is_available": "eq.true", "is_active": "eq.true"}),
    }
    recent_leads = await bot.select("leads", {"order": "created_at.desc", "limit": "5"})
    recent_sessions = await bot.select(
        "chat_sessions", {"select": "*,counselor:counselors(id,name,email)", "order": "created_at.desc", "limit": "5"}
    )
    return {"stats": stats, "recent_leads": recent_leads, "recent_sessions": recent_sessions}


def _session_filters(status: Optional[str], source: Optional[str], search: Optional[str]) -> dict:
    params: dict = {}
    if status == "ended":
        status = "closed"
    if status:
        if status not in SESSION_STATUSES:
            raise ValidationError("Unknown session status")
        params["status"] = f"eq.{status}"
    if source:
        params["source"] = f"eq.{re.sub(r'[^a-z_]', '', source.lower())}"
    if search:
        term = re.sub(r"[^\w+]", "", search)[:40]
        if term:
            params["phone"] = f"ilike.*{term}*"
    return params


async def list_sessions(bot: BotDB, status: Optional[str] = None, source: Optional[str] = None,
                        search: Optional[str] = None, limit: int = 100) -> list[dict]:
    params = {
        "select": "*,counselor:counselors(id,name,email)",
        "order": "updated_at.desc",
        "limit": str(limit),
        **_session_filters(status, source, search),
    }
    sessions = await bot.select("chat_sessions", params)
    if not sessions:
        return []
    # One query for every session's last message and count, instead of two per session.
    ids = [s["id"] for s in sessions]
    messages = await bot.select("chat_messages", {
        "select": "session_id,message,sender,created_at",
        "session_id": in_list(ids),
        "order": "created_at.desc",
        "limit": "10000",
    })
    last: dict = {}
    counts: dict = {}
    for m in messages:
        counts[m["session_id"]] = counts.get(m["session_id"], 0) + 1
        last.setdefault(m["session_id"], {k: m[k] for k in ("message", "sender", "created_at")})
    for s in sessions:
        s["last_message"] = last.get(s["id"])
        s["message_count"] = counts.get(s["id"], 0)
    return sessions


async def get_session(bot: BotDB, session_id: str) -> dict:
    check_id(session_id, "session id")
    session = await bot.one("chat_sessions", {"select": "*,counselor:counselors(id,name,email)", "id": f"eq.{session_id}"})
    if not session:
        raise NotFoundError("Chat session not found")
    return session


async def session_messages(bot: BotDB, session_id: str) -> list[dict]:
    await get_session(bot, session_id)
    return await bot.select("chat_messages", {"session_id": f"eq.{session_id}", "order": "created_at.asc", "limit": "2000"})


async def list_leads(bot: BotDB, status: Optional[str] = None, source: Optional[str] = None,
                     qualification: Optional[str] = None, date_from: Optional[str] = None,
                     date_to: Optional[str] = None) -> list[dict]:
    params: dict = {"order": "created_at.desc", "limit": "2000"}
    if status:
        params["status"] = f"eq.{status}"
    if source:
        params["source"] = f"eq.{source}"
    if qualification:
        params["qualification"] = f'eq.{qualification}'
    created = []
    if date_from:
        created.append(f"created_at.gte.{date_from}")
    if date_to:
        created.append(f"created_at.lte.{date_to}T23:59:59")
    if created:
        params["and"] = "(" + ",".join(created) + ")"
    return await bot.select("leads", params)


async def list_counselors(bot: BotDB) -> list[dict]:
    return await bot.select("counselors", {"order": "name.asc"})


async def pending_handovers(bot: BotDB) -> list[dict]:
    return await bot.select("handovers", {
        "select": "*,counselor:counselors(name),lead:leads(phone,name)",
        "status": "eq.pending", "order": "assigned_at.desc",
    })


def to_csv(rows: list[dict], columns: list[str]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        values = []
        for col in columns:
            value = row
            for part in col.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            values.append("" if value is None else value)
        writer.writerow(values)
    return out.getvalue()


LEAD_CSV = ["name", "phone", "email", "qualification", "pathway", "field", "course_interest",
            "source", "status", "notes", "created_at"]
CHAT_CSV = ["phone", "source", "status", "counselor.name", "message_count", "last_message.message",
            "handed_over_at", "closed_at", "created_at", "updated_at"]
