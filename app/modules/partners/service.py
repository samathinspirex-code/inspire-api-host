"""Partner link builder, click stats and CRM matches via referral_code."""

import hashlib
import re
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.modules.insights.demo_data import PARTNER_PLATFORMS, PARTNER_TYPES

SITE = "https://inspirecollege.lk"


def _code(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]", "", name).upper()[:8] or "PARTNER"
    suffix = hashlib.sha256(name.encode()).hexdigest()[:4].upper()
    return f"{base}{suffix}"


def _destination(code: str, platform: str, partner_type: str, campaign: str, programme: str | None) -> str:
    medium = partner_type.lower().replace(" ", "_")
    camp = quote((campaign or "always-on").lower().replace(" ", "-"))
    if platform == "all":
        return f"{SITE}/go/{code}?utm_medium={medium}&utm_campaign={camp}&ref={code}"
    path = f"/programmes/{quote(programme)}" if programme else "/apply"
    return f"{SITE}{path}?utm_source={quote(platform)}&utm_medium={medium}&utm_campaign={camp}&ref={code}"


async def list_links(db: AsyncSession) -> dict:
    links = (await db.execute(text(
        "SELECT link_id, name, partner_type, platform, programme, campaign, commission, code, url, created_at "
        "FROM partner_links ORDER BY created_at DESC"
    ))).mappings().all()
    clicks = {row[0]: int(row[1]) for row in (await db.execute(text(
        "SELECT code, count(*) FROM partner_link_clicks GROUP BY code"
    ))).all()}
    leads = {row[0]: {"leads": int(row[1]), "enrolled": int(row[2]), "revenue": float(row[3] or 0)} for row in (await db.execute(text(
        "SELECT referral_code, count(*), count(*) FILTER (WHERE stage = 'enrolled'), "
        "coalesce(sum(programme_fee) FILTER (WHERE stage = 'enrolled'), 0) "
        "FROM crm_leads WHERE referral_code IS NOT NULL AND is_archived = false GROUP BY referral_code"
    ))).all()}
    rows = []
    for link in links:
        code = link["code"]
        matched = leads.get(code, {"leads": 0, "enrolled": 0, "revenue": 0})
        commission = float(link["commission"] or 0)
        cost = matched["enrolled"] * commission
        rows.append({
            "id": link["link_id"],
            "name": link["name"],
            "type": link["partner_type"],
            "platform": link["platform"],
            "programme": link["programme"],
            "campaign": link["campaign"],
            "commission": commission,
            "code": code,
            "url": link["url"],
            "share_url": f"{settings.PUBLIC_API_BASE_URL.rstrip('/')}/go/{code}",
            "created_at": link["created_at"].isoformat() if link["created_at"] else None,
            "clicks": clicks.get(code, 0),
            "leads": matched["leads"],
            "enrolled": matched["enrolled"],
            "revenue": matched["revenue"],
            "roi": round(matched["revenue"] / cost, 2) if cost else None,
            "source": "cms",
        })
    return {
        "links": rows,
        "types": PARTNER_TYPES,
        "platforms": PARTNER_PLATFORMS,
        "totals": {
            "links": len(rows),
            "clicks": sum(item["clicks"] for item in rows),
            "leads": sum(item["leads"] for item in rows),
            "enrolled": sum(item["enrolled"] for item in rows),
            "revenue": sum(item["revenue"] for item in rows),
        },
    }


async def create_link(db: AsyncSession, payload: dict, user_id: int) -> dict:
    name = (payload.get("name") or "").strip()
    partner_type = (payload.get("type") or "").strip()
    platform = (payload.get("platform") or "all").strip()
    if not name or partner_type not in PARTNER_TYPES:
        raise ValidationError("Provide a partner name and a known partner type")
    if platform not in {item["id"] for item in PARTNER_PLATFORMS}:
        raise ValidationError("Unknown platform")
    code = _code(name)
    existing = (await db.execute(text("SELECT 1 FROM partner_links WHERE code = :code"), {"code": code})).first()
    if existing:
        code = f"{code[:8]}{hashlib.sha256(f'{name}{user_id}'.encode()).hexdigest()[:4].upper()}"
    url = _destination(code, platform, partner_type, payload.get("campaign") or "Always on", payload.get("programme"))
    row = (await db.execute(text(
        "INSERT INTO partner_links (name, partner_type, platform, programme, campaign, commission, code, url, created_by) "
        "VALUES (:name, :type, :platform, :programme, :campaign, :commission, :code, :url, :user) "
        "RETURNING link_id"
    ), {
        "name": name, "type": partner_type, "platform": platform,
        "programme": (payload.get("programme") or None), "campaign": payload.get("campaign") or "Always on",
        "commission": float(payload.get("commission") or 0), "code": code, "url": url, "user": user_id,
    })).one()
    await db.commit()
    return {"id": row[0], "code": code, "url": url, "share_url": f"{settings.PUBLIC_API_BASE_URL.rstrip('/')}/go/{code}"}


async def delete_link(db: AsyncSession, link_id: int) -> None:
    result = await db.execute(text("DELETE FROM partner_links WHERE link_id = :id"), {"id": link_id})
    await db.commit()
    if result.rowcount == 0:
        raise NotFoundError("Partner link not found")


async def record_click(db: AsyncSession, code: str, platform: str | None, utm: dict, ip_hash: str | None) -> str | None:
    link = (await db.execute(text("SELECT url FROM partner_links WHERE lower(code) = lower(:code)"), {"code": code})).first()
    if not link:
        return None
    await db.execute(text(
        "INSERT INTO partner_link_clicks (code, platform, utm_source, utm_medium, utm_campaign, ip_hash) "
        "VALUES (:code, :platform, :src, :med, :camp, :ip)"
    ), {"code": code, "platform": platform, "src": utm.get("utm_source"), "med": utm.get("utm_medium"), "camp": utm.get("utm_campaign"), "ip": ip_hash})
    await db.commit()
    return link[0]


async def link_for_caption(db: AsyncSession, link_id: int) -> dict:
    row = (await db.execute(text(
        "SELECT name, partner_type, platform, programme, campaign, url, code FROM partner_links WHERE link_id = :id"
    ), {"id": link_id})).mappings().first()
    if not row:
        raise NotFoundError("Partner link not found")
    return dict(row)
