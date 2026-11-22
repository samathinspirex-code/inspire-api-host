"""Website-wide settings managed in the CMS, with a public read-only projection for the website."""
import copy
import json
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import require_access
from app.modules.auth.schemas.auth import CurrentUser

cms_router = APIRouter(prefix="/api/v1/cms/site-settings", tags=["cms"])
public_router = APIRouter(prefix="/api/v1/public/site-settings", tags=["public-site-settings"])

WHATSAPP = "whatsapp"  # The website swaps this for its "chat with an advisor" WhatsApp link.

# Home page hero slides as they were hard-coded on the website before they moved to the CMS.
DEFAULT_HERO_SLIDES: list[dict] = [
    {
        "id": "free-coffee", "status": "active", "tag": "· Free Coffee with Barista ·",
        "title": "Your Future ", "title_highlight": "is Brewing.", "title_break": False, "title_single_line": False,
        "subtitle": "Study hard. Sip free.",
        "description": "Free coffee for every enrolled Inspire College student at participating Barista cafés. Weekday daytime only, funded by Inspire College.",
        "benefits": ["1 Coffee a Week", "4 Coffees a Month", "48 Coffees a Year"],
        "primary_label": "Apply now →", "primary_link": "/admissions",
        "secondary_label": "Chat with an advisor", "secondary_link": WHATSAPP,
        "image": "/home-hero-free-coffee.webp",
        "image_alt": "Inspire College student holding a free Barista coffee, with 1 coffee a week, 4 a month and 48 a year",
        "badge_top": "FREE COFFEE", "badge_bottom_eyebrow": "Weekday daytime", "badge_bottom_value": "At Barista cafés",
    },
    {
        "id": "online-university", "status": "active", "tag": "· Sri Lanka's First Online University ·",
        "title": "Your Online ", "title_highlight": "University", "title_break": False, "title_single_line": False,
        "subtitle": "Foundation · HND · Degree · Master’s · CPD",
        "description": "100% online qualifications and professional courses through ATHE, an Ofqual-regulated UK awarding organisation, international university partners, and CPD programmes.",
        "benefits": ["100% Online", "Flexible", "Globally Recognised"],
        "primary_label": "Apply now →", "primary_link": "/admissions",
        "secondary_label": "Chat with an advisor", "secondary_link": WHATSAPP,
        "image": "/hero-banner1.png", "image_alt": "Inspire College student beginning his pathway to a global qualification",
        "badge_top": "100% ONLINE", "badge_bottom_eyebrow": "Seats filling fast", "badge_bottom_value": "First 20 at LKR 250K",
    },
    {
        "id": "ai-journey", "status": "active", "tag": "· Your AI Journey ·",
        "title": "From AI Student ", "title_highlight": "to AI Engineer", "title_break": False, "title_single_line": True,
        "subtitle": "Explore flexible learning pathways",
        "description": "Learn practical AI skills online and build toward the career you want.",
        "benefits": ["Practical AI Skills", "Flexible Learning", "Career Growth"],
        "primary_label": "Explore Programs →", "primary_link": "/programs",
        "secondary_label": "Chat with an Advisor", "secondary_link": WHATSAPP,
        "image": "/ai-student-engineer-hero.png", "image_alt": "AI student progressing toward a career as an AI engineer",
        "badge_top": "AI ENGINEER", "badge_bottom_eyebrow": "Career Ready", "badge_bottom_value": "AI Student",
    },
    {
        "id": "business-journey", "status": "active", "tag": "· Your Business Journey ·",
        "title": "From Business Student", "title_highlight": "to Business Professional", "title_break": True, "title_single_line": False,
        "subtitle": "Foundation · HND · Degree pathways",
        "description": "Build practical business skills online and grow toward the career you want.",
        "benefits": ["Business Skills", "Flexible Learning", "Career Growth"],
        "primary_label": "Explore Business Programmes →", "primary_link": "/programs?school=Business",
        "secondary_label": "Chat with an Advisor", "secondary_link": WHATSAPP,
        "image": "/business-student-professional-hero.png", "image_alt": "Business student progressing toward a professional career",
        "badge_top": "BUSINESS PROFESSIONAL", "badge_bottom_eyebrow": "Career Ready", "badge_bottom_value": "Business Student",
    },
    {
        "id": "programmes", "status": "active", "tag": "· Globally Accredited Portfolio ·",
        "title": "Explore Academic ", "title_highlight": "Programmes", "title_break": False, "title_single_line": False,
        "subtitle": "Foundation · HND Pathways · Top-Up Degrees · Short Courses",
        "description": "Discover career-ready diplomas, UK-validated HND pathways, Top-Up degrees and AI-powered short courses. Learn anytime, anywhere with interactive live classes and self-paced LMS.",
        "benefits": ["Foundation", "HND & Degrees", "Short Courses", "AI Mastery"],
        "primary_label": "Explore all programmes →", "primary_link": "/programs",
        "secondary_label": "Check entry criteria", "secondary_link": "/admissions",
        "image": "/home-hero-programmes.png", "image_alt": "Inspire College professional developing career-ready skills through online study",
        "badge_top": "UK ACCREDITED", "badge_bottom_eyebrow": "Flexible Learning", "badge_bottom_value": "Study From Anywhere",
    },
    {
        "id": "parents", "status": "active", "tag": "· For Every Parent Who Believes ·",
        "title": "Their Journey. ", "title_highlight": "Your Proud Moment.", "title_break": True, "title_single_line": False,
        "subtitle": "Foundation · HND · Degree pathways",
        "description": "Help your child take the next step with flexible online learning and guidance throughout their studies.",
        "benefits": ["Foundation", "HND", "Degree Pathways"],
        "primary_label": "Explore Programmes", "primary_link": "/programs",
        "secondary_label": "Chat with an Advisor", "secondary_link": WHATSAPP,
        "image": "/home-hero-about.png", "image_alt": "Inspire College graduate celebrating success with family",
        "badge_top": "", "badge_bottom_eyebrow": "Student Success", "badge_bottom_value": "100% Satisfaction",
    },
]

DEFAULT_PROMO_BANNER: dict = {
    "enabled": True,
    "desktop_image": "/free-coffee-banner-desktop.jpg",
    "mobile_image": "/free-coffee-banner-mobile.jpg",
    "alt": "Your Future is Brewing. Free Coffee for every enrolled Inspire College student: 1 coffee a week, 4 coffees a month, 48 coffees a year.",
    "link": "",
}

DEFAULTS: dict = {
    "christmas_theme": False,
    # Top announcement strip shown above the menu on every page (visitors can close it).
    "announcement_enabled": False,
    "announcement_text": "",
    "announcement_link": "/programs",
    # Home page hero carousel. Only "active" slides show on the website; "draft" and "hidden" stay in the CMS.
    "hero_slides": DEFAULT_HERO_SLIDES,
    # Wide promotion banner at the bottom of the home page.
    "promo_banner": DEFAULT_PROMO_BANNER,
}


def _check_url(value: str, message: str, *, allow_empty: bool = False, allow_whatsapp: bool = False) -> str:
    value = (value or "").strip()
    if not value and allow_empty:
        return ""
    if allow_whatsapp and value.lower() == WHATSAPP:
        return WHATSAPP
    if value.startswith("/") and not value.startswith("//"):
        return value
    parsed = urlsplit(value)
    if parsed.scheme == "https" and parsed.hostname:
        return value
    raise ValueError(message)


LINK_MESSAGE = "Use a site path such as /programs, a full https:// link, or whatsapp"
IMAGE_MESSAGE = "Upload an image, or use a website image path such as /hero.png"


class HeroSlideInput(BaseModel):
    id: str = Field(..., min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    status: Literal["active", "draft", "hidden"] = "draft"
    tag: str = Field("", max_length=80)
    title: str = Field("", max_length=120)
    title_highlight: str = Field("", max_length=120)
    title_break: bool = False
    title_single_line: bool = False
    subtitle: str = Field("", max_length=160)
    description: str = Field("", max_length=400)
    benefits: list[str] = Field(default_factory=list, max_length=6)
    primary_label: str = Field("", max_length=60)
    primary_link: str = Field("", max_length=500)
    secondary_label: str = Field("", max_length=60)
    secondary_link: str = Field("", max_length=500)
    image: str = Field("", max_length=1000)
    image_alt: str = Field("", max_length=250)
    badge_top: str = Field("", max_length=40)
    badge_bottom_eyebrow: str = Field("", max_length=40)
    badge_bottom_value: str = Field("", max_length=40)

    # "title" keeps its trailing space on purpose: it separates the title from the highlighted words.
    @field_validator("tag", "title_highlight", "subtitle", "description", "primary_label", "secondary_label", "image_alt", "badge_top", "badge_bottom_eyebrow", "badge_bottom_value")
    @classmethod
    def strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("benefits")
    @classmethod
    def clean_benefits(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if any(len(item) > 40 for item in cleaned):
            raise ValueError("Keep each benefit to 40 characters or fewer")
        return cleaned

    @field_validator("primary_link", "secondary_link")
    @classmethod
    def safe_links(cls, value: str) -> str:
        return _check_url(value, LINK_MESSAGE, allow_empty=True, allow_whatsapp=True)

    @field_validator("image")
    @classmethod
    def safe_image(cls, value: str) -> str:
        return _check_url(value, IMAGE_MESSAGE, allow_empty=True)

    @model_validator(mode="after")
    def ready_to_publish(self):
        # Drafts may be half-finished; a slide shown on the website needs its essentials.
        if self.status == "active":
            needed = (
                ("title", (self.title + self.title_highlight).strip()),
                ("image", self.image),
                ("main button text", self.primary_label),
                ("main button link", self.primary_link),
            )
            missing = [label for label, value in needed if not value]
            if missing:
                name = (self.title + self.title_highlight).strip() or self.id
                raise ValueError(f"Slide '{name}' can't be active yet. Add: {', '.join(missing)}")
        return self


class PromoBannerInput(BaseModel):
    enabled: bool = True
    desktop_image: str = Field("", max_length=1000)
    mobile_image: str = Field("", max_length=1000)
    alt: str = Field("", max_length=300)
    link: str = Field("", max_length=500)

    @field_validator("desktop_image", "mobile_image")
    @classmethod
    def safe_image(cls, value: str) -> str:
        return _check_url(value, IMAGE_MESSAGE, allow_empty=True)

    @field_validator("link")
    @classmethod
    def safe_link(cls, value: str) -> str:
        return _check_url(value, LINK_MESSAGE, allow_empty=True, allow_whatsapp=True)

    @field_validator("alt")
    @classmethod
    def strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def needs_image(self):
        if self.enabled and not self.desktop_image:
            raise ValueError("Add a desktop banner image before showing the banner")
        return self


class SiteSettingsInput(BaseModel):
    """Partial update: only the fields sent are changed."""

    christmas_theme: bool | None = None
    announcement_enabled: bool | None = None
    announcement_text: str | None = Field(None, max_length=200)
    announcement_link: str | None = Field(None, max_length=500)
    hero_slides: list[HeroSlideInput] | None = Field(None, max_length=12)
    promo_banner: PromoBannerInput | None = None

    @field_validator("announcement_text")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("announcement_link")
    @classmethod
    def safe_link(cls, value):
        if value is None:
            return value
        return _check_url(value.strip() or "/programs", "Use a site path such as /programs or a full https:// link")

    @field_validator("hero_slides")
    @classmethod
    def at_least_one_live(cls, value):
        if value is None:
            return value
        if not any(slide.status == "active" for slide in value):
            raise ValueError("Keep at least one hero slide active so the top of the home page is never empty")
        ids = [slide.id for slide in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Each hero slide needs its own id")
        return value


async def _read(db: AsyncSession) -> dict:
    rows = (await db.execute(text("SELECT key, value FROM site_settings WHERE key = ANY(:keys)"), {"keys": list(DEFAULTS)})).mappings().all()
    settings = copy.deepcopy(DEFAULTS)
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
    if not isinstance(settings["hero_slides"], list) or not settings["hero_slides"]:
        settings["hero_slides"] = copy.deepcopy(DEFAULT_HERO_SLIDES)
    if not isinstance(settings["promo_banner"], dict):
        settings["promo_banner"] = copy.deepcopy(DEFAULT_PROMO_BANNER)
    return settings


@public_router.get("")
async def public_settings(response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "public, max-age=60"
    settings = await _read(db)
    # Draft and hidden slides never leave the CMS.
    settings["hero_slides"] = [slide for slide in settings["hero_slides"] if isinstance(slide, dict) and slide.get("status") == "active"]
    banner = settings["promo_banner"]
    settings["promo_banner"] = banner if banner.get("enabled") and banner.get("desktop_image") else {"enabled": False}
    return settings


@cms_router.get("", dependencies=[Depends(require_access("CMS_ADMIN"))])
async def cms_settings(db: AsyncSession = Depends(get_db)):
    return await _read(db)


@cms_router.put("")
async def update_settings(
    payload: SiteSettingsInput,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_access("CMS_ADMIN")),
):
    for key, value in payload.model_dump(exclude_none=True).items():
        await db.execute(text("""
            INSERT INTO site_settings (key, value, updated_at, updated_by) VALUES (:key, CAST(:value AS jsonb), now(), :user_id)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by
        """), {"key": key, "value": json.dumps(value), "user_id": current_user.user_id})
    await db.commit()
    return await _read(db)
