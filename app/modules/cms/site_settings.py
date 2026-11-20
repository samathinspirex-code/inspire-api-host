"""Website-wide settings managed in the CMS, with a public read-only projection for the website."""
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import require_access
from app.modules.auth.schemas.auth import CurrentUser

cms_router = APIRouter(prefix="/api/v1/cms/site-settings", tags=["cms"])
public_router = APIRouter(prefix="/api/v1/public/site-settings", tags=["public-site-settings"])

DEFAULTS: dict = {
    "christmas_theme": False,
    # Top announcement strip shown above the menu on every page (visitors can close it).
    "announcement_enabled": False,
    "announcement_text": "",
    "announcement_link": "/programs",
}


class SiteSettingsInput(BaseModel):
    """Partial update: only the fields sent are changed."""

    christmas_theme: bool | None = None
    announcement_enabled: bool | None = None
    announcement_text: str | None = Field(None, max_length=200)
    announcement_link: str | None = Field(None, max_length=500)

    @field_validator("announcement_text")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("announcement_link")
    @classmethod
    def safe_link(cls, value):
        if value is None:
            return value
        value = value.strip() or "/programs"
        if value.startswith("/") and not value.startswith("//"):
            return value
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname:
            return value
        raise ValueError("Use a site path such as /programs or a full https:// link")


async def _read(db: AsyncSession) -> dict:
    rows = (await db.execute(text("SELECT key, value FROM site_settings WHERE key = ANY(:keys)"), {"keys": list(DEFAULTS)})).mappings().all()
    settings = dict(DEFAULTS)
    for row in rows:
        value = row["value"]
        # The driver normally decodes JSONB already; only decode when a raw JSON string slips through.
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        settings[row["key"]] = value
    settings["christmas_theme"] = bool(settings["christmas_theme"])
    settings["announcement_enabled"] = bool(settings["announcement_enabled"])
    settings["announcement_text"] = str(settings["announcement_text"] or "")
    settings["announcement_link"] = str(settings["announcement_link"] or "/programs")
    return settings


@public_router.get("")
async def public_settings(response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "public, max-age=60"
    return await _read(db)


@cms_router.get("", dependencies=[Depends(require_access("CMS"))])
async def cms_settings(db: AsyncSession = Depends(get_db)):
    return await _read(db)


@cms_router.put("")
async def update_settings(
    payload: SiteSettingsInput,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_access("CMS")),
):
    for key, value in payload.model_dump(exclude_none=True).items():
        await db.execute(text("""
            INSERT INTO site_settings (key, value, updated_at, updated_by) VALUES (:key, CAST(:value AS jsonb), now(), :user_id)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by
        """), {"key": key, "value": json.dumps(value), "user_id": current_user.user_id})
    await db.commit()
    return await _read(db)
