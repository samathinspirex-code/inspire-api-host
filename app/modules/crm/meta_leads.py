"""Brings new Meta lead form submissions into the CRM. Polls every few minutes; no public endpoint.

Only leads submitted after intake first runs are brought in. Each Meta lead ID is created once.
Answers from Meta are stored as data and never treated as instructions.
"""
import json
import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ValidationError
from app.modules.connectors.providers import ProviderFailure, _meta_get

logger = logging.getLogger(__name__)

SOURCE = "meta_lead_forms"
LEAD_FIELDS = "id,created_time,field_data,ad_name,campaign_name,form_id,platform,is_organic"
LOCK_MINUTES = 10
PROGRAMME_QUESTION = re.compile(r"programme|program|course|interest|which|study", re.I)
PLATFORMS = {"fb": "Facebook", "ig": "Instagram", "msgr": "Messenger", "messenger": "Messenger", "wa": "WhatsApp", "whatsapp": "WhatsApp"}


def is_configured() -> bool:
    return bool(settings.CONNECTORS_META_LEADS_TOKEN and settings.CONNECTORS_META_PAGE_ID)


def _clean(value, limit: int = 255) -> str | None:
    text_value = re.sub(r"\s+", " ", str(value or "")).strip()
    return text_value[:limit] or None


def map_lead(lead: dict, form_name: str | None) -> dict:
    """Meta's answers to CRM fields. Unknown questions go into the message as plain text."""
    answers = {}
    for item in lead.get("field_data") or []:
        values = [str(value) for value in item.get("values") or [] if str(value).strip()]
        if item.get("name") and values:
            answers[str(item["name"])] = ", ".join(values)
    known = {"full_name", "first_name", "last_name", "phone_number", "email", "city"}
    name = answers.get("full_name") or " ".join(filter(None, (answers.get("first_name"), answers.get("last_name"))))
    programme = next((value for key, value in answers.items() if key not in known and PROGRAMME_QUESTION.search(key)), None)
    extra = [f"{key.replace('_', ' ')}: {value}" for key, value in answers.items() if key not in known and value != programme]
    platform = PLATFORMS.get(str(lead.get("platform") or "").lower(), _clean(lead.get("platform"), 30) or "Meta")
    notes = [f"Meta lead form: {_clean(form_name, 120) or lead.get('form_id')}", f"Platform: {platform}"]
    if lead.get("ad_name"):
        notes.append(f"Ad: {_clean(lead['ad_name'], 120)}")
    if lead.get("is_organic"):
        notes.append("Organic (not from an ad)")
    return {
        "full_name": _clean(name) or "Meta lead",
        "phone": _clean(answers.get("phone_number"), 50),
        "email": (_clean(answers.get("email")) or "").lower() or None,
        "city": _clean(answers.get("city"), 100),
        "interested_programme": _clean(programme),
        "message": _clean("\n".join(extra), 2000) if extra else None,
        "campaign": _clean(lead.get("campaign_name")),
        "notes": ". ".join(notes) + ".",
        "social_lead_id": str(lead["id"]),
        "platform": platform,
    }


async def _pages(client, path: str, token: str, params: dict) -> list[dict]:
    rows, after = [], None
    for _ in range(50):  # A safety stop; one page holds up to 100 items.
        data = await _meta_get(client, path, token, {**params, "limit": 100, **({"after": after} if after else {})})
        rows += data.get("data") or []
        paging = data.get("paging") or {}
        after = (paging.get("cursors") or {}).get("after") if paging.get("next") else None
        if not after:
            break
    return rows


async def _page_token(client, token: str, page_id: str) -> str:
    """Page endpoints need the Page's own token, which the System User token can hand out for an assigned Page."""
    me = await _meta_get(client, "me", token, {"fields": "id"})
    if str(me.get("id")) == page_id:
        return token  # Already a Page token.
    data = await _meta_get(client, "me/accounts", token, {"fields": "id,access_token", "limit": 100})
    match = next((item for item in data.get("data") or [] if str(item.get("id")) == page_id and item.get("access_token")), None)
    if not match:
        raise ProviderFailure("The leads token cannot act for this Page. Assign the Page to the System User in Business Settings.")
    return match["access_token"]


async def _claim(db: AsyncSession) -> datetime | None:
    """A lock row rather than an advisory lock, so it works through the database pooler. Returns when intake began."""
    await db.execute(text("INSERT INTO crm_meta_lead_state (id, started_at) VALUES (1, now()) ON CONFLICT (id) DO NOTHING"))
    started = await db.scalar(text(f"""
        UPDATE crm_meta_lead_state SET running_until = now() + interval '{LOCK_MINUTES} minutes', last_run_at = now()
        WHERE id = 1 AND (running_until IS NULL OR running_until < now()) RETURNING started_at
    """))
    await db.commit()
    return started


async def _finish(db: AsyncSession, forms: int | None, error: str | None) -> None:
    await db.execute(text("""
        UPDATE crm_meta_lead_state SET running_until = NULL, last_error = CAST(:error AS VARCHAR),
            forms_watched = COALESCE(CAST(:forms AS INTEGER), forms_watched),
            last_success_at = CASE WHEN CAST(:error AS VARCHAR) IS NULL THEN now() ELSE last_success_at END
        WHERE id = 1
    """), {"error": error, "forms": forms})
    await db.commit()


async def _existing_lead(db: AsyncSession, phone: str | None) -> int | None:
    from app.modules.crm.service import _normalized_lead_phone
    normalized = _normalized_lead_phone(phone)
    if len(normalized) < 7:
        return None
    rows = (await db.execute(text("""
        SELECT lead_id, phone FROM crm_leads
        WHERE is_archived = false AND right(regexp_replace(coalesce(phone, ''), '[^0-9]', '', 'g'), 9) = :suffix
        ORDER BY lead_id DESC
    """), {"suffix": normalized[-9:]})).all()
    return next((row[0] for row in rows if _normalized_lead_phone(row[1]) == normalized), None)


async def _bring_in(db: AsyncSession, lead: dict, form_name: str | None) -> str:
    from app.modules.crm.schemas import CrmLeadCreate
    from app.modules.crm.service import create_lead
    if await db.scalar(text("SELECT 1 FROM crm_leads WHERE social_lead_id = :id"), {"id": str(lead["id"])}):
        return "duplicate"
    mapped = map_lead(lead, form_name)
    existing = await _existing_lead(db, mapped["phone"])
    if existing:
        # Same person again: note it on their open lead rather than adding a second one.
        await db.execute(text("INSERT INTO crm_activities (lead_id, activity_type, content) VALUES (:id, 'note', :content)"),
                         {"id": existing, "content": f"Submitted a Meta lead form again ({mapped['notes']}) Meta lead ID {mapped['social_lead_id']}."})
        await db.commit()
        return "matched"
    platform = mapped.pop("platform")
    payload = CrmLeadCreate(**mapped, source=SOURCE, stage="new_lead", priority="medium")
    try:
        await create_lead(db, payload, counsellor_id=None, counsellor_name=f"Meta Lead Forms ({platform})")
    except IntegrityError:
        await db.rollback()
        return "duplicate"
    return "created"


async def run_once(session_factory, *, transport=None) -> dict:
    if not is_configured():
        return {"skipped": "Meta lead intake is not configured."}
    token, page_id = settings.CONNECTORS_META_LEADS_TOKEN, settings.CONNECTORS_META_PAGE_ID
    if not re.fullmatch(r"[0-9]+", page_id):
        return {"skipped": "CONNECTORS_META_PAGE_ID must be the numeric Page ID."}
    async with session_factory() as db:
        started = await _claim(db)
    if started is None:
        return {"skipped": "Another run is in progress."}
    started_ts = int(started.timestamp()) if started.tzinfo else int(started.replace(tzinfo=timezone.utc).timestamp())
    totals = {"created": 0, "matched": 0, "duplicate": 0, "failed": 0, "forms": 0}
    error = None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            token = await _page_token(client, token, page_id)
            forms = await _pages(client, f"{page_id}/leadgen_forms", token, {"fields": "id,name,status"})
            totals["forms"] = len(forms)
            for form in forms:
                async with session_factory() as db:
                    cursor = await db.scalar(text("SELECT last_created FROM crm_meta_lead_cursors WHERE form_id = :id"), {"id": str(form["id"])})
                # A form seen for the first time starts from when intake began, so nothing older comes in.
                since = int(cursor) if cursor is not None else started_ts
                leads = await _pages(client, f"{form['id']}/leads", token, {
                    "fields": LEAD_FIELDS, "filtering": json.dumps([{"field": "time_created", "operator": "GREATER_THAN", "value": since}])})
                newest = since
                for lead in sorted(leads, key=lambda item: item["created_time"]):
                    created = int(datetime.strptime(lead["created_time"], "%Y-%m-%dT%H:%M:%S%z").timestamp())
                    if created <= since:
                        continue
                    async with session_factory() as db:
                        try:
                            totals[await _bring_in(db, lead, form.get("name"))] += 1
                        except (ValidationError, ValueError, KeyError, TypeError) as exc:
                            await db.rollback()
                            totals["failed"] += 1
                            logger.warning("Meta lead %s could not be added: %s", lead.get("id"), type(exc).__name__)
                    newest = max(newest, created)
                async with session_factory() as db:
                    await db.execute(text("""
                        INSERT INTO crm_meta_lead_cursors (form_id, last_created) VALUES (:id, :ts)
                        ON CONFLICT (form_id) DO UPDATE SET last_created = GREATEST(crm_meta_lead_cursors.last_created, EXCLUDED.last_created), updated_at = now()
                    """), {"id": str(form["id"]), "ts": newest})
                    await db.commit()
    except ProviderFailure as exc:
        error = str(exc)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        error = "Meta lead intake failed. Check the leads token, Page ID and Page access."
    async with session_factory() as db:
        await _finish(db, totals["forms"] if error is None else None, error or (f"{totals['failed']} leads could not be added." if totals["failed"] else None))
    return {**totals, **({"error": error} if error else {})}


async def status(db: AsyncSession) -> dict:
    base = {"configured": is_configured()}
    if not await db.scalar(text("SELECT to_regclass('public.crm_meta_lead_state') IS NOT NULL")):
        return {**base, "installed": False}
    row = (await db.execute(text("SELECT last_run_at, last_success_at, last_error, forms_watched, started_at FROM crm_meta_lead_state WHERE id = 1"))).mappings().first()
    created = await db.scalar(text("SELECT count(*) FROM crm_leads WHERE source = :source AND social_lead_id IS NOT NULL AND created_at >= now() - interval '30 days'"), {"source": SOURCE})
    iso = lambda value: value.isoformat() if value else None
    return {**base, "installed": True, "created_30d": int(created or 0), "forms_watched": (row or {}).get("forms_watched"),
            "last_run_at": iso((row or {}).get("last_run_at")), "last_success_at": iso((row or {}).get("last_success_at")),
            "started_at": iso((row or {}).get("started_at")), "last_error": (row or {}).get("last_error")}
