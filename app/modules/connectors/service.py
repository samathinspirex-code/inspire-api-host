import json
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.connectors.capi import status as capi_status
from app.modules.connectors.registry import REQUIREMENTS, account_state, platforms_for, tracking_plan
from app.modules.connectors.providers import ProviderFailure, fetch_report, ga4_realtime
from app.modules.insights.website import latest_report
from app.modules.insights.website import ga4_events
from app.modules.insights.finance import load_finance
from app.modules.insights.lms import load_lms
from app.modules.insights.whatsapp import load_whatsapp


def _configured(account: dict) -> bool:
    if account["platform"] == "gtm":
        return True  # Reads only public URLs.
    if account["platform"] == "email":
        return bool(settings.MAILJET_API_KEY and settings.MAILJET_SECRET_KEY)
    if account["platform"] == "meta":
        return bool(settings.CONNECTORS_META_TOKEN)  # One token covers every ad account assigned to it.
    return bool(settings.CONNECTORS_CREDENTIALS.get(str(account["connector_id"]), {}).get("refresh_token")
                and settings.CONNECTORS_GOOGLE_CLIENT_ID and settings.CONNECTORS_GOOGLE_CLIENT_SECRET)


def _credentials(account: dict) -> dict:
    if account["platform"] == "meta":
        return {"token": settings.CONNECTORS_META_TOKEN}
    if account["platform"] == "email":
        return {"key": settings.MAILJET_API_KEY, "secret": settings.MAILJET_SECRET_KEY}
    return settings.CONNECTORS_CREDENTIALS.get(str(account["connector_id"]), {})


async def saved_accounts(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(text(
        "SELECT connector_id, platform, name, account_id, owner, status, created_at, last_synced_at, last_error, report "
        "FROM connectors ORDER BY created_at DESC, connector_id DESC"
    ))).mappings().all()
    accounts = []
    for row in rows:
        account = dict(row)
        account.update(account_state(account, _configured(account)))
        for key in ("created_at", "last_synced_at"):
            if account[key]:
                account[key] = account[key].isoformat()
        account["source"] = "connector" if account["last_synced_at"] else "unavailable"
        accounts.append(account)
    return accounts


async def reporting(db: AsyncSession) -> dict:
    accounts = await saved_accounts(db)
    whatsapp = await load_whatsapp(db)
    lms = await load_lms(db)
    finance = await load_finance(db)
    capi = await capi_status(db)
    return {"accounts": accounts, "platforms": platforms_for(accounts, whatsapp, lms, finance, capi), "events": tracking_plan(ga4_events(accounts)), "whatsapp": whatsapp, "lms": lms, "finance": finance, "capi": capi}


_REALTIME_TTL = 60.0
_realtime_cache: tuple[float, dict] | None = None


async def website_now(db: AsyncSession) -> dict:
    """Live GA4 totals for On the site now. Any failure simply reports it as unavailable."""
    global _realtime_cache
    if _realtime_cache and time.monotonic() - _realtime_cache[0] < _REALTIME_TTL:
        return _realtime_cache[1]
    account = latest_report(await saved_accounts(db), "ga4")
    credentials = settings.CONNECTORS_CREDENTIALS.get(str(account["connector_id"]), {}) if account else {}
    if not credentials.get("refresh_token") or not settings.CONNECTORS_GOOGLE_CLIENT_ID:
        return {"available": False}
    try:
        result = await ga4_realtime(account["account_id"], credentials, settings.CONNECTORS_GOOGLE_CLIENT_ID, settings.CONNECTORS_GOOGLE_CLIENT_SECRET)
    except (ProviderFailure, ValidationError, httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return {"available": False}
    _realtime_cache = (time.monotonic(), result)
    return result


async def catalogue(db: AsyncSession) -> dict:
    data = await reporting(db)
    lines = (await db.execute(text(
        "SELECT w.line_id, w.counsellor_user_id, w.phone_number, w.display_name, w.phone_number_id, "
        "coalesce(u.full_name, u.email) "
        "FROM whatsapp_lines w LEFT JOIN users u ON u.user_id = w.counsellor_user_id ORDER BY w.created_at DESC"
    ))).all()
    counsellors = (await db.execute(text(
        "SELECT DISTINCT u.user_id, coalesce(nullif(u.full_name, ''), u.email) FROM users u "
        "JOIN user_access_levels ual ON ual.user_id = u.user_id "
        "JOIN access_levels al ON al.access_level_id = ual.access_level_id AND al.access_key = 'COUNSELLOR' "
        "WHERE u.is_active = true ORDER BY 2"
    ))).all()
    return {
        **data,
        "whatsapp_lines": [
            {"id": row[0], "counsellor_user_id": row[1], "number": row[2], "display": row[3], "phone_number_id": row[4], "counsellor": row[5], "source": "cms"}
            for row in lines
        ],
        "counsellors": [{"user_id": row[0], "name": row[1]} for row in counsellors],
    }


async def add_account(db: AsyncSession, payload: dict, user_id: int) -> dict:
    known = set(REQUIREMENTS)
    if payload["platform"] not in known:
        raise ValidationError("Unknown platform")
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValidationError("Enter an account name")
    row = (await db.execute(text(
        "INSERT INTO connectors (platform, name, account_id, owner, created_by) "
        "VALUES (:platform, :name, :account, :owner, :user) RETURNING connector_id"
    ), {"platform": payload["platform"], "name": name, "account": (payload.get("account_id") or "").strip() or None,
        "owner": (payload.get("owner") or "").strip() or None, "user": user_id})).one()
    await db.commit()
    return {"id": row[0]}


async def sync_account(db: AsyncSession, connector_id: int) -> dict:
    # A transaction-scoped row lock prevents concurrent syncs and deletion races.
    try:
        row = (await db.execute(text("SELECT * FROM connectors WHERE connector_id = :id FOR UPDATE NOWAIT"),
                                {"id": connector_id})).mappings().first()
    except DBAPIError as exc:
        await db.rollback()
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise ConflictError("This account is already syncing. Try again shortly.") from None
        raise
    if not row:
        raise NotFoundError("Connector not found")
    account = dict(row)
    try:
        report = await fetch_report(account["platform"], account["account_id"],
                                    _credentials(account),
                                    settings.CONNECTORS_GOOGLE_CLIENT_ID, settings.CONNECTORS_GOOGLE_CLIENT_SECRET)
    except ValidationError:
        await db.rollback()
        raise
    except (ProviderFailure, httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
        # Never persist provider bodies, URLs or exception strings containing credentials.
        message = str(exc) if isinstance(exc, ProviderFailure) else "Reporting failed. Check provider availability and account configuration."
        await db.execute(text("UPDATE connectors SET status = 'sync_error', last_error = :error WHERE connector_id = :id"),
                         {"id": connector_id, "error": message})
        await db.commit()
        raise ConflictError(message)
    synced = datetime.now(timezone.utc)
    await db.execute(text("UPDATE connectors SET status = 'connected', last_error = NULL, last_synced_at = :synced, "
                          "report = CAST(:report AS jsonb) WHERE connector_id = :id"),
                     {"id": connector_id, "synced": synced, "report": json.dumps(report, allow_nan=False)})
    await db.commit()
    return {"id": connector_id, "last_synced_at": synced.isoformat(), "report": report}


async def sync_all(session_factory) -> list[dict]:
    """Each account gets its own session, so one failure cannot affect the others."""
    async with session_factory() as db:
        accounts = await saved_accounts(db)
    results = []
    for account in accounts:
        if not account["can_sync"]:
            continue
        async with session_factory() as db:
            try:
                await sync_account(db, account["connector_id"])
                outcome = "synced"
            except (ConflictError, ValidationError, NotFoundError) as exc:
                outcome = f"failed: {exc}"
        results.append({"id": account["connector_id"], "platform": account["platform"], "outcome": outcome})
    return results


async def delete_account(db: AsyncSession, connector_id: int) -> None:
    result = await db.execute(text("DELETE FROM connectors WHERE connector_id = :id"), {"id": connector_id})
    await db.commit()
    if result.rowcount == 0:
        raise NotFoundError("Connector not found")


async def add_line(db: AsyncSession, payload: dict, user_id: int) -> dict:
    number = (payload.get("number") or "").strip()
    if len(number) < 6:
        raise ValidationError("Enter the WhatsApp number")
    row = (await db.execute(text(
        "INSERT INTO whatsapp_lines (counsellor_user_id, phone_number, display_name, phone_number_id, created_by) "
        "VALUES (:counsellor, :number, :display, :pid, :user) RETURNING line_id"
    ), {
        "counsellor": payload.get("counsellor_user_id"),
        "number": number,
        "display": payload.get("display") or None,
        "pid": payload.get("phone_number_id") or None,
        "user": user_id,
    })).one()
    await db.commit()
    return {"id": row[0]}


async def delete_line(db: AsyncSession, line_id: int) -> None:
    result = await db.execute(text("DELETE FROM whatsapp_lines WHERE line_id = :id"), {"id": line_id})
    await db.commit()
    if result.rowcount == 0:
        raise NotFoundError("WhatsApp line not found")
