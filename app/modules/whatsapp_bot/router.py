"""CRM -> WhatsApp Bot API. Viewing: CRM & Admissions (CRM) or Admissions Counsellor (COUNSELLOR).
Running an import (POST /sync): CRM & Admissions only.

Read-only on the bot: every route only reads the bot's database. The one write,
POST /sync, writes CRM leads (and the CRM's own record of them), never the bot.
Leads and pending handovers carry `crm_lead_id` when the bot lead is already in the CRM.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import ForbiddenError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.whatsapp_bot import service
from app.modules.whatsapp_bot.supabase import BotDB, BotUnavailable, configured
from app.modules.whatsapp_bot.sync import imported_by_bot_lead, pick_assignee, run_sync, sync_status


VIEW_ROLES = {"CRM", "COUNSELLOR"}


async def require_whatsapp_bot(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    # CRM & Admissions and Admissions Counsellors may view. Admin roles alone do not grant it.
    if not VIEW_ROLES.intersection(user.access):
        raise ForbiddenError("WhatsApp Bot access is required")
    return user


async def require_whatsapp_bot_manager(user: CurrentUser = Depends(require_whatsapp_bot)) -> CurrentUser:
    if "CRM" not in user.access:
        raise ForbiddenError("Only CRM & Admissions users can run an import")
    return user


async def bot_db():
    async with BotDB() as bot:
        yield bot


router = APIRouter(
    prefix="/api/v1/cms/whatsapp-bot",
    tags=["whatsapp-bot"],
    dependencies=[Depends(require_whatsapp_bot)],
)


def _csv(body: str, name: str) -> Response:
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    assignee = await pick_assignee(db, preview=True)
    await db.rollback()  # The preview takes a row lock; release it.
    info = {"configured": configured(), "reachable": False, "error": None,
            "assign_to": (assignee or {}).get("name") or (assignee or {}).get("email"), **await sync_status(db)}
    if info["configured"]:
        try:
            async with BotDB() as bot:
                await bot.count("counselors")
            info["reachable"] = True
        except BotUnavailable as exc:
            info["error"] = exc.message
    else:
        info["error"] = "Set WABOT_SUPABASE_URL and WABOT_SUPABASE_SERVICE_ROLE_KEY on the API."
    return info


@router.get("/dashboard")
async def dashboard(bot: BotDB = Depends(bot_db)):
    return await service.dashboard(bot)


@router.get("/sessions")
async def sessions(
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=60),
    bot: BotDB = Depends(bot_db),
):
    return {"data": await service.list_sessions(bot, status, source, search)}


@router.get("/sessions/{session_id}/messages")
async def messages(session_id: str, bot: BotDB = Depends(bot_db)):
    return {"data": await service.session_messages(bot, session_id)}


@router.get("/leads")
async def leads(
    status: Optional[str] = Query(None, max_length=50),
    source: Optional[str] = Query(None, max_length=50),
    qualification: Optional[str] = Query(None, max_length=100),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    bot: BotDB = Depends(bot_db),
    db: AsyncSession = Depends(get_db),
):
    rows = await service.list_leads(
        bot, status, source, qualification,
        date_from.isoformat() if date_from else None, date_to.isoformat() if date_to else None,
    )
    done = await imported_by_bot_lead(db, [r["id"] for r in rows])
    for row in rows:
        row["crm_lead_id"] = done.get(row["id"])
    return {"data": rows}


@router.get("/counsellors")
async def counsellors(bot: BotDB = Depends(bot_db)):
    return {"data": await service.list_counselors(bot)}


@router.get("/handovers/pending")
async def pending(bot: BotDB = Depends(bot_db), db: AsyncSession = Depends(get_db)):
    rows = await service.pending_handovers(bot)
    done = await imported_by_bot_lead(db, [r["lead_id"] for r in rows if r.get("lead_id")])
    for row in rows:
        row["crm_lead_id"] = done.get(row.get("lead_id"))
    return {"data": rows}


@router.get("/export/chats.csv")
async def export_chats(
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=60),
    bot: BotDB = Depends(bot_db),
):
    rows = await service.list_sessions(bot, status, source, search, limit=1000)
    return _csv(service.to_csv(rows, service.CHAT_CSV), "whatsapp-bot-chats.csv")


@router.get("/export/leads.csv")
async def export_leads(
    status: Optional[str] = Query(None, max_length=50),
    source: Optional[str] = Query(None, max_length=50),
    qualification: Optional[str] = Query(None, max_length=100),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    bot: BotDB = Depends(bot_db),
):
    rows = await service.list_leads(
        bot, status, source, qualification,
        date_from.isoformat() if date_from else None, date_to.isoformat() if date_to else None,
    )
    return _csv(service.to_csv(rows, service.LEAD_CSV), "whatsapp-bot-leads.csv")


@router.get("/sync/preview")
async def sync_preview(db: AsyncSession = Depends(get_db)):
    """What the next import would copy, without writing anything."""
    return await run_sync(db, dry_run=True)


@router.post("/sync")
async def sync_now(_: CurrentUser = Depends(require_whatsapp_bot_manager), db: AsyncSession = Depends(get_db)):
    return await run_sync(db)
