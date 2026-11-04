"""Meta Conversions API: a queue the CRM never waits on.

A worker finds consented leads and enrolments, then sends them. Nothing is sent unless it is switched on.
Only hashed phone and email, Meta's own click IDs, the event name and time, and an enrolment's fee ever leave.
"""
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.connectors.providers import META_API

logger = logging.getLogger(__name__)

CONSENT_WORDING_VERSION = "2026-11-draft"
WEBSITE_SOURCES = ("website_admission", "website_contact")
LEAD_FORM_SOURCES = ("meta", "meta_lead_forms")
MAX_EVENT_AGE = timedelta(days=7)  # Meta rejects older events.
ENQUEUE_WINDOW = "6 days"
MAX_ATTEMPTS = 8
BATCH_SIZE = 100
MAX_BATCHES = 5
LOCK_ID = 748201
CLICK_ID = re.compile(r"^fb\.\d+\.\d+\.[A-Za-z0-9_.\-]{1,200}$")


def is_configured() -> bool:
    return bool(settings.CONNECTORS_META_CAPI_TOKEN and settings.CONNECTORS_META_PIXEL_ID)


def is_enabled() -> bool:
    return is_configured() and settings.CONNECTORS_META_CAPI_ENABLED


def normalize_phone(value: str | None) -> str | None:
    """Digits with country code. Bare Sri Lankan numbers gain 94."""
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    elif len(digits) == 10 and digits.startswith("0"):
        digits = "94" + digits[1:]
    elif len(digits) == 9 and digits.startswith("7"):
        digits = "94" + digits
    return digits if 8 <= len(digits) <= 15 else None


def normalize_email(value: str | None) -> str | None:
    email = (value or "").strip().lower()
    return email if "@" in email else None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _click_id(value: str | None) -> str | None:
    return value if value and CLICK_ID.match(value) else None


def _fee(value) -> float | None:
    try:
        fee = float(re.sub(r"[^\d.]", "", str(value)))
    except (TypeError, ValueError):
        return None
    return fee if fee > 0 else None


def build_event(row: dict) -> dict | None:
    """The whole payload for one event, or None if there is nothing to match on."""
    phone, email = normalize_phone(row.get("phone")), normalize_email(row.get("email"))
    if not phone and not email:
        return None
    user_data = {}
    if phone:
        user_data["ph"] = [_hash(phone)]
    if email:
        user_data["em"] = [_hash(email)]
    if fbc := _click_id(row.get("fbc")):
        user_data["fbc"] = fbc
    if fbp := _click_id(row.get("fbp")):
        user_data["fbp"] = fbp
    custom = {"event_source": "crm", "lead_event_source": "Inspire CMS"}
    if row["event_name"] == "Enrolled" and (fee := _fee(row.get("programme_fee"))):
        custom.update(value=fee, currency="LKR")
    return {"event_name": row["event_name"], "event_time": int(row["event_time"].timestamp()),
            "event_id": f"inspire-{row['lead_id']}-{row['event_name'].lower()}", "action_source": "system_generated",
            "user_data": user_data, "custom_data": custom}


async def record_consent(db: AsyncSession, form: str, email, phone, consented: bool, fbc=None, fbp=None) -> None:
    """Called after a form has saved. A failure here must never fail the person's form."""
    if not consented:
        return
    try:
        await db.execute(text("""
            INSERT INTO meta_capi_consents (email, phone, form, wording_version, fbc, fbp)
            VALUES (:email, :phone, :form, :version, :fbc, :fbp)
        """), {"email": normalize_email(str(email) if email else None), "phone": (phone or "").strip() or None, "form": form,
               "version": CONSENT_WORDING_VERSION, "fbc": _click_id(fbc), "fbp": _click_id(fbp)})
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - the form already succeeded.
        await db.rollback()
        logger.warning("Could not record ad consent: %s", type(exc).__name__)


async def enqueue(db: AsyncSession) -> None:
    await db.execute(text("""
        UPDATE meta_capi_consents c SET lead_id = (
            SELECT l.lead_id FROM crm_leads l
            WHERE lower(l.email) = c.email AND (c.phone IS NULL OR l.phone = c.phone) AND l.source = ANY(:sources)
              AND l.created_at <= c.consented_at + interval '10 minutes'
            ORDER BY l.created_at DESC LIMIT 1)
        WHERE c.lead_id IS NULL
    """), {"sources": list(WEBSITE_SOURCES)})
    await db.execute(text("UPDATE meta_capi_consents SET email = NULL, phone = NULL WHERE lead_id IS NOT NULL AND (email IS NOT NULL OR phone IS NOT NULL)"))
    await db.execute(text("DELETE FROM meta_capi_consents WHERE lead_id IS NULL AND consented_at < now() - interval '7 days'"))
    await db.execute(text(f"""
        INSERT INTO meta_capi_events (lead_id, event_name, event_time)
        SELECT l.lead_id, 'Lead', l.created_at FROM crm_leads l
        WHERE l.is_archived = false AND l.source = ANY(:sources) AND l.created_at >= now() - interval '{ENQUEUE_WINDOW}'
          AND EXISTS (SELECT 1 FROM meta_capi_consents c WHERE c.lead_id = l.lead_id)
        ON CONFLICT (lead_id, event_name) DO NOTHING
    """), {"sources": list(WEBSITE_SOURCES)})
    # Meta's own lead forms only count once compliance has approved them as consent.
    lead_forms = list(LEAD_FORM_SOURCES) if settings.CONNECTORS_META_CAPI_LEAD_FORMS else []
    await db.execute(text(f"""
        INSERT INTO meta_capi_events (lead_id, event_name, event_time)
        SELECT l.lead_id, 'Enrolled', l.enrolled_at FROM crm_leads l
        WHERE l.is_archived = false AND l.stage = 'enrolled' AND l.enrolled_at >= now() - interval '{ENQUEUE_WINDOW}'
          AND (l.source = ANY(:lead_forms) OR EXISTS (SELECT 1 FROM meta_capi_consents c WHERE c.lead_id = l.lead_id))
        ON CONFLICT (lead_id, event_name) DO NOTHING
    """), {"lead_forms": lead_forms})


def _allowed(row: dict) -> bool:
    if row["is_archived"]:
        return False
    return bool(row["has_consent"]) or (settings.CONNECTORS_META_CAPI_LEAD_FORMS and row["source"] in LEAD_FORM_SOURCES)


async def _post(events: list[dict], transport=None, test_code: str | None = None) -> tuple[bool, str | None]:
    """Returns (delivered, fixed message). Provider bodies are never kept."""
    body = {"data": events, **({"test_event_code": test_code} if test_code else {})}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            response = await client.post(f"{META_API}/{settings.CONNECTORS_META_PIXEL_ID}/events", json=body,
                                         headers={"Authorization": f"Bearer {settings.CONNECTORS_META_CAPI_TOKEN}"})
    except httpx.HTTPError:
        return False, "Could not reach Meta. Will retry."
    if response.is_success:
        return True, None
    if response.status_code == 429 or response.status_code >= 500:
        return False, "Meta was busy or rate limited. Will retry."
    if response.status_code in (401, 403):
        return False, "Meta rejected the token. Check the Conversions API token and the Pixel ID."
    return False, "Meta rejected this batch. Check the token, Pixel ID and event settings."


async def _send_batch(db: AsyncSession, transport=None) -> dict:
    rows = (await db.execute(text("""
        SELECT e.event_id, e.lead_id, e.event_name, e.event_time, e.attempts, l.phone, l.email, l.source, l.is_archived, l.programme_fee,
               c.fbc, c.fbp, c.consent_id IS NOT NULL AS has_consent
        FROM meta_capi_events e JOIN crm_leads l ON l.lead_id = e.lead_id
        LEFT JOIN LATERAL (SELECT fbc, fbp, consent_id FROM meta_capi_consents WHERE lead_id = e.lead_id ORDER BY consented_at DESC LIMIT 1) c ON true
        WHERE e.status = 'pending' AND e.next_attempt_at <= now()
        ORDER BY e.event_id LIMIT :limit
    """), {"limit": BATCH_SIZE})).mappings().all()
    now = datetime.now(timezone.utc)
    outcome = {"sent": 0, "retry": 0, "dropped": 0}
    ready = []

    async def close(event_id: int, status_name: str) -> None:
        await db.execute(text("UPDATE meta_capi_events SET status = :status WHERE event_id = :id"), {"status": status_name, "id": event_id})
        outcome["dropped"] += 1

    for item in rows:
        row = dict(item)
        event_time = row["event_time"]
        row["event_time"] = event_time if event_time.tzinfo else event_time.replace(tzinfo=timezone.utc)
        if not _allowed(row):
            await close(row["event_id"], "cancelled")
        elif now - row["event_time"] >= MAX_EVENT_AGE:
            await close(row["event_id"], "expired")
        elif (event := build_event(row)) is None:
            await close(row["event_id"], "unmatchable")
        else:
            ready.append((row, event))
    if not ready:
        return {**outcome, "more": len(rows) == BATCH_SIZE}
    delivered, message = await _post([event for _, event in ready], transport)
    ids = [row["event_id"] for row, _ in ready]
    if delivered:
        await db.execute(text("UPDATE meta_capi_events SET status = 'sent', sent_at = now(), attempts = attempts + 1, last_error = NULL WHERE event_id = ANY(:ids)"), {"ids": ids})
        outcome["sent"] = len(ids)
    else:
        # Backs off to about 4 hours, then gives up, since old events cannot arrive in time anyway.
        await db.execute(text("""
            UPDATE meta_capi_events SET attempts = attempts + 1, last_error = :error,
                status = CASE WHEN attempts + 1 >= :max THEN 'failed' ELSE 'pending' END,
                next_attempt_at = now() + make_interval(mins => LEAST(240, 15 * power(2, attempts)::int))
            WHERE event_id = ANY(:ids)
        """), {"ids": ids, "error": message, "max": MAX_ATTEMPTS})
        outcome["retry"] = len(ids)
    return {**outcome, "more": delivered and len(rows) == BATCH_SIZE}


async def run_once(session_factory, *, transport=None) -> dict:
    if not is_enabled():
        return {"skipped": "Sending is switched off."}
    totals = {"sent": 0, "retry": 0, "dropped": 0}
    async with session_factory() as db:
        if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:id)"), {"id": LOCK_ID}):
            return {"skipped": "Another run is in progress."}
        await enqueue(db)
        await db.commit()
    for _ in range(MAX_BATCHES):
        async with session_factory() as db:
            # Held through the send, so overlapping runs cannot send the same batch.
            if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:id)"), {"id": LOCK_ID}):
                return {**totals, "skipped": "Another run is in progress."}
            result = await _send_batch(db, transport)
            await db.commit()
        for key in totals:
            totals[key] += result[key]
        if not result["more"]:
            break
    return totals


async def send_test_event(test_code: str, *, transport=None) -> tuple[bool, str | None]:
    """A made-up person, sent only to Meta's Test Events tab. No lead is read."""
    if not is_configured():
        return False, "Add the Conversions API token and Pixel ID first."
    now = int(time.time())
    event = {"event_name": "Enrolled", "event_time": now, "event_id": f"inspire-test-{now}", "action_source": "system_generated",
             "user_data": {"ph": [_hash("94000000000")]}, "custom_data": {"event_source": "crm", "lead_event_source": "Inspire CMS"}}
    return await _post([event], transport, test_code)


async def withdraw(db: AsyncSession, lead_id: int) -> None:
    await db.execute(text("DELETE FROM meta_capi_consents WHERE lead_id = :id"), {"id": lead_id})
    await db.execute(text("UPDATE meta_capi_events SET status = 'cancelled' WHERE lead_id = :id AND status = 'pending'"), {"id": lead_id})
    await db.commit()


async def status(db: AsyncSession) -> dict:
    base = {"configured": is_configured(), "enabled": is_enabled(), "lead_forms": bool(settings.CONNECTORS_META_CAPI_LEAD_FORMS)}
    # Checked first, so a missing table cannot abort the transaction behind every page.
    if not await db.scalar(text("SELECT to_regclass('public.meta_capi_events') IS NOT NULL AND to_regclass('public.meta_capi_consents') IS NOT NULL")):
        return {**base, "installed": False, "pending": 0, "sent_30d": 0, "failed_30d": 0, "consents_30d": 0, "last_sent_at": None, "last_error": None}
    row = (await db.execute(text("""
        SELECT count(*) FILTER (WHERE status = 'pending') AS pending,
               count(*) FILTER (WHERE status = 'sent' AND sent_at >= now() - interval '30 days') AS sent_30d,
               count(*) FILTER (WHERE status = 'failed' AND created_at >= now() - interval '30 days') AS failed_30d,
               max(sent_at) AS last_sent_at,
               (SELECT last_error FROM meta_capi_events WHERE last_error IS NOT NULL AND status IN ('pending', 'failed') ORDER BY event_id DESC LIMIT 1) AS last_error,
               (SELECT count(*) FROM meta_capi_consents WHERE consented_at >= now() - interval '30 days') AS consents_30d
        FROM meta_capi_events
    """))).mappings().first()
    return {**base, "installed": True, "pending": int(row["pending"]), "sent_30d": int(row["sent_30d"]), "failed_30d": int(row["failed_30d"]), "consents_30d": int(row["consents_30d"]),
            "last_sent_at": row["last_sent_at"].isoformat() if row["last_sent_at"] else None, "last_error": row["last_error"]}
