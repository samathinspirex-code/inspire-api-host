"""Website content for the new site: settings, the one-call bundle, FAQs, offers, stories and news."""
import copy
import json
import time
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.modules.academic.service import list_courses
from app.modules.cms.site_settings import _read as read_site_settings
from . import detail as D
from .pricing import fee_table, validate_fee_plans

# Website-only settings. Announcement strip, Christmas theme, hero and bottom banner are shared with the
# current website and stay in Website settings (site_settings).
DEFAULTS: dict = {
    "contact": {"whatsapp_main": "94760974225", "whatsapp_advisor": "94760974225", "phone_display": "+94 76 097 4225", "email": "enrol@inspire.college"},
    "theme": {"palette": "purple", "shape": "", "font": ""},
    "tracking": {"gtm": "", "ga4": "", "metaPixel": "", "tiktokPixel": "", "googleAdsId": "", "googleAdsPurchaseLabel": "", "googleAdsLeadLabel": ""},
    "default_campaign": "scholarship",
    "home_extra_cards": [],
    "seo": {"site_name": "Inspire College", "default_description": "100% online UK-regulated ATHE qualifications, HNDs, Foundations, top-up degrees, postgraduate and CPD courses."},
    "fee_plans": {},
    # Story cards: the design photo and focus for each testimonial (matched by name). Video and caption come from Testimonials.
    "stories": [],
}
DICT_KEYS = {"contact", "theme", "tracking", "seo"}

_cache: dict = {}
CACHE_SECONDS = 60


def clear_cache() -> None:
    _cache.clear()


def jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [jsonable(x) for x in v]
    if hasattr(v, "as_integer_ratio") and not isinstance(v, (int, bool)):
        return float(v)
    return v


async def settings(db: AsyncSession) -> dict:
    rows = (await db.execute(text("SELECT key, value FROM website_settings"))).mappings().all()
    out = copy.deepcopy(DEFAULTS)
    for r in rows:
        if r["key"] not in DEFAULTS:
            continue
        v = r["value"]
        out[r["key"]] = {**out[r["key"]], **v} if r["key"] in DICT_KEYS and isinstance(v, dict) else v
    return out


def _clean_setting(key: str, value):
    if key not in DEFAULTS:
        raise ValidationError(f"Unknown website setting: {key}")
    if key in DICT_KEYS:
        if not isinstance(value, dict) or any(not isinstance(v, str) or len(v) > 400 for v in value.values()):
            raise ValidationError(f"{key} must be a set of short text values")
        allowed = set(DEFAULTS[key])
        value = {k: v.strip() for k, v in value.items() if k in allowed}
        if key == "contact":
            for k in ("whatsapp_main", "whatsapp_advisor"):
                if k in value and value[k] and not (value[k].isdigit() and 9 <= len(value[k]) <= 15):
                    raise ValidationError("WhatsApp numbers are digits only with the country code, e.g. 94760974225")
        return value
    if key == "default_campaign":
        if not isinstance(value, str) or len(value) > 40:
            raise ValidationError("Pick a campaign key")
        return value.strip()
    if key == "fee_plans":
        return validate_fee_plans(value)
    if key in ("home_extra_cards", "stories"):
        if not isinstance(value, list) or len(value) > 40 or any(not isinstance(x, dict) for x in value):
            raise ValidationError(f"{key} must be a list (max 40)")
        if len(json.dumps(value)) > 60_000:
            raise ValidationError(f"{key} is too large")
        return value
    return value


async def put_settings(db: AsyncSession, patch: dict, user_id: int | None) -> dict:
    for key, value in (patch or {}).items():
        value = _clean_setting(key, value)
        await db.execute(text("""INSERT INTO website_settings(key, value, updated_by) VALUES (:k, CAST(:v AS jsonb), :u)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now(), updated_by=EXCLUDED.updated_by"""),
                         {"k": key, "v": json.dumps(value), "u": user_id})
    await db.commit()
    clear_cache()
    return await settings(db)


async def website_rows(db: AsyncSession) -> dict[int, dict]:
    rows = (await db.execute(text("SELECT * FROM website_courses"))).mappings().all()
    return {r["course_id"]: dict(r) for r in rows}


async def courses(db: AsyncSession, include_hidden: bool = False) -> list[dict]:
    """Every active catalogue course as the website sees it (hidden listings left out unless asked)."""
    cat = await list_courses(db, active_only=True)
    web = await website_rows(db)
    out = []
    for i, c in enumerate(cat):
        w = web.get(c["course_id"])
        if w and w.get("web_status") == "hidden" and not include_hidden:
            continue
        item = D.compose(c, w, i)
        if include_hidden:
            item["web_status"] = (w or {}).get("web_status", "visible")
            item["has_listing"] = bool(w)
            item["programme_code"] = (w or {}).get("programme_code") or ""
            item["fee_override"] = (w or {}).get("fee_override")
        out.append(item)
    out.sort(key=lambda x: (x["sort_order"], x["title"]))
    return out


async def course_by_key(db: AsyncSession, key: str) -> dict | None:
    return next((c for c in await courses(db) if c["key"] == key), None)


async def _published(db: AsyncSession, table: str) -> list[dict]:
    rows = (await db.execute(text(f"SELECT * FROM {table} WHERE status='published' ORDER BY sort_order, id"))).mappings().all()
    return [jsonable(dict(r)) for r in rows]


def news_item(r: dict, full: bool = False) -> dict:
    content = r.get("content") or []
    body = "\n\n".join(p for p in content if isinstance(p, str)) if isinstance(content, list) else str(content or "")
    pub = r.get("published_on") or r.get("event_date") or r.get("created_at")
    out = {"slug": r["slug"], "title": r["title"], "excerpt": r.get("excerpt") or "", "category": r.get("category") or r.get("kind") or "News",
           "image": r.get("image_url") or "", "published_at": jsonable(pub), "seo_title": r.get("meta_title") or "",
           "seo_description": r.get("meta_description") or ""}
    if full:
        out.update(body=body, video_url=r.get("video_url") or "", video_orientation=r.get("video_orientation") or "",
                   video_poster_url=r.get("video_poster_url") or "")
    return out


async def news(db: AsyncSession, limit: int = 100) -> list[dict]:
    rows = (await db.execute(text("""SELECT * FROM news_events WHERE status='Published'
        ORDER BY COALESCE(published_on, event_date, created_at::date) DESC, news_event_id DESC LIMIT :n"""), {"n": limit})).mappings().all()
    return [news_item(dict(r)) for r in rows]


async def news_one(db: AsyncSession, slug: str) -> dict | None:
    r = (await db.execute(text("SELECT * FROM news_events WHERE slug=:s AND status='Published'"), {"s": slug})).mappings().first()
    return news_item(dict(r), full=True) if r else None


async def stories(db: AsyncSession, overrides: list) -> list[dict]:
    """Published testimonials, with the design photo and focus from Website settings -> stories when a name matches."""
    rows = (await db.execute(text("SELECT * FROM cms_testimonials WHERE status='Published' ORDER BY position, testimonial_id"))).mappings().all()
    by_name = {str(o.get("name", "")).strip().lower(): o for o in overrides or [] if isinstance(o, dict)}
    out = []
    for r in rows:
        o = by_name.get((r["name"] or "").strip().lower(), {})
        out.append({"name": r["name"], "role": o.get("role") or r["programme"], "image": o.get("image") or r["thumbnail_url"],
                    "image_pos": o.get("image_pos") or "50%", "video_url": r["video_url"] or o.get("video_url") or "",
                    "caption": r["caption"] or ""})
    return out


async def bundle(db: AsyncSession) -> dict:
    hit = _cache.get("bundle")
    if hit and hit[0] > time.monotonic():
        return hit[1]
    s = await settings(db)
    shared = await read_site_settings(db)
    banner = shared.get("promo_banner") or {}
    public_settings = {k: v for k, v in s.items() if k not in ("fee_plans", "stories")}
    public_settings.update({
        "announcement_enabled": bool(shared.get("announcement_enabled")), "announcement_text": shared.get("announcement_text") or "",
        "announcement_link": shared.get("announcement_link") or "", "christmas_theme": bool(shared.get("christmas_theme")),
        "promo_banner": {"enabled": bool(banner.get("enabled")), "title": "", "text": "", "cta": "", "link": banner.get("link") or "",
                         "image": banner.get("desktop_image") or "", "mobile_image": banner.get("mobile_image") or "", "alt": banner.get("alt") or ""},
        "hero_slides": [x for x in shared.get("hero_slides") or [] if x.get("status") == "active"],
    })
    out = jsonable({
        "settings": public_settings,
        "fee_plans": fee_table(s.get("fee_plans")),
        "courses": await courses(db),
        "testimonials": await stories(db, s.get("stories")),
        "leaders": [],
        "faqs": await _published(db, "website_faqs"),
        "campaigns": await _published(db, "website_campaigns"),
        "news": await news(db, 12),
    })
    _cache["bundle"] = (time.monotonic() + CACHE_SECONDS, out)
    return out


async def readiness(db: AsyncSession) -> dict:
    """Problems that would break or empty a page on the new website."""
    items = await courses(db)
    keys = {c["key"] for c in items}
    missing = [k for k in D.REQUIRED_KEYS if k not in keys]
    no_fee = [c["title"] for c in items if c["plan_type"] == "cpd" and not c["fee"]]
    no_image = [c["title"] for c in items if c["featured"] and not c["image"]]
    return {"ok": not missing, "courses": len(items), "missing_design_keys": missing,
            "short_courses_without_fee": no_fee[:20], "featured_without_image": no_image[:20]}
