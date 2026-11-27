"""Public routes for the new website (/api/v1/public/web/...) and the PayHere hand-off page (/pay/web/<token>/go)."""
import html
import json
import logging
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.errors import APIError, ConflictError, NotFoundError, ValidationError
from app.modules.payments.db import transaction
from . import checkout, crm_sync, demo, leads, service
from .guards import limit, require
from .pricing import addon_price, billing_terms, money, plans_for

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/public/web", tags=["new-website"])
handoff_router = APIRouter(tags=["new-website"])
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


def _no_store(response_headers) -> None:
    response_headers.update(NO_STORE)


# ---------------------------------------------------------------- content
@router.get("/bundle", dependencies=[Depends(require("WEB_API_ENABLED"))])
async def bundle(db: AsyncSession = Depends(get_db)):
    return await service.bundle(db)


@router.get("/courses/{slug}", dependencies=[Depends(require("WEB_API_ENABLED"))])
async def course(slug: str, db: AsyncSession = Depends(get_db)):
    item = next((c for c in await service.courses(db) if c["slug"] == slug), None)
    if not item:
        raise NotFoundError("Course not found")
    return item


@router.get("/news", dependencies=[Depends(require("WEB_API_ENABLED"))])
async def news(db: AsyncSession = Depends(get_db)):
    return await service.news(db)


@router.get("/news/{slug}", dependencies=[Depends(require("WEB_API_ENABLED"))])
async def news_item(slug: str, db: AsyncSession = Depends(get_db)):
    item = await service.news_one(db, slug)
    if not item:
        raise NotFoundError("News item not found")
    return item


# ---------------------------------------------------------------- leads
class LeadIn(BaseModel):
    kind: str = "contact"
    name: str = Field("", max_length=200)
    email: str = Field("", max_length=200)
    phone: str = Field("", max_length=60)
    course_key: str = Field("", max_length=200)
    message: str = Field("", max_length=4000)
    data: dict = Field(default_factory=dict)


@router.post("/leads", dependencies=[Depends(require("WEB_API_ENABLED"))])
async def lead(body: LeadIn, request: Request, db: AsyncSession = Depends(get_db)):
    limit(request, "web-lead", 12, 600)
    if body.kind not in ("enrol", "contact", "aptitude", "admissions", "callback"):
        raise ValidationError("Unknown form")
    if not body.email.strip() and len("".join(ch for ch in body.phone if ch.isdigit())) < 9:
        raise ValidationError("Please add an email or a phone number")
    course = await service.course_by_key(db, body.course_key) if body.course_key else None
    data = body.data if isinstance(body.data, dict) else {}
    extra = ", ".join(f"{k}: {v}" for k, v in data.items() if k in ("who", "plan", "method", "scholarship", "referral", "ref", "page") and v)[:400]
    await leads.upsert(db, kind=body.kind, name=body.name, email=body.email, phone=body.phone, course_id=course["course_id"] if course else None,
                       message=body.message, plan=str(data.get("plan") or "")[:200], utm=data.get("utm") if isinstance(data.get("utm"), dict) else None,
                       referral=str(data.get("referral") or data.get("ref") or "")[:40], extra_note=extra)
    return {"ok": True}


class DraftIn(BaseModel):
    name: str = Field(..., max_length=200)
    email: str = Field(..., max_length=200)
    phone: str = Field(..., max_length=60)
    course_key: str = Field("", max_length=200)
    plan: str = Field("", max_length=200)
    utm: dict = Field(default_factory=dict)


@router.post("/leads/draft", dependencies=[Depends(require("WEB_API_ENABLED"))])
async def lead_draft(body: DraftIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Called once name, email and phone are filled in at checkout, before any payment."""
    limit(request, "web-draft", 10, 600)
    if len(body.name.strip()) < 2 or "@" not in body.email or len("".join(ch for ch in body.phone if ch.isdigit())) < 9:
        raise ValidationError("Name, email and phone are needed")
    course = await service.course_by_key(db, body.course_key) if body.course_key else None
    await leads.upsert(db, kind="checkout_started", name=body.name, email=body.email, phone=body.phone,
                       course_id=course["course_id"] if course else None, plan=body.plan, utm=body.utm or None,
                       extra_note=course["title"] if course else "")
    return {"ok": True}


# ---------------------------------------------------------------- enrolment orders
class OrderIn(BaseModel):
    course_key: str = Field(..., max_length=200)
    plan: int = 0
    addons: list[str] = Field(default_factory=list)
    free: list[str] = Field(default_factory=list)
    method: str = "payhere"
    name: str = Field(..., max_length=200)
    phone: str = Field(..., max_length=60)
    email: str = Field("", max_length=200)
    quoted_amount: float | None = None
    extra: dict = Field(default_factory=dict)


def handoff_url(token: str) -> str:
    return f"{settings.PAYMENTS_PUBLIC_BASE_URL.rstrip('/')}/pay/web/{token}/go"


async def _send_offer(lead_id: int) -> None:
    async with AsyncSessionLocal() as db:
        await leads.send_offer_once(db, lead_id)


@router.post("/orders", dependencies=[Depends(require("WEB_CHECKOUT_ENABLED"))])
async def create_order(body: OrderIn, request: Request, background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Enrol from the website. The server prices everything; nothing reaches the ledger until money is received."""
    limit(request, "web-order", 10, 600)
    if body.method not in ("payhere", "bank_transfer"):
        raise ValidationError("Unknown payment method")
    digits = "".join(ch for ch in body.phone if ch.isdigit())
    if len(body.name.strip()) < 2 or len(digits) < 9 or "@" not in body.email or len(body.email.strip()) < 5:
        raise ValidationError("Please add your full name, email and a WhatsApp number.")
    course = await service.course_by_key(db, body.course_key)
    if not course:
        raise NotFoundError("Programme not found")
    s = await service.settings(db)
    plans = plans_for(course, s.get("fee_plans"))
    plan = plans[min(max(body.plan, 0), len(plans) - 1)]
    terms = billing_terms(plan, course["plan_type"])
    web_rows = await service.website_rows(db)
    lines = [dict(course_id=course["course_id"], title=course["title"], plan_type=course["plan_type"], kind="main", amount=terms["today"],
                  programme_code=(web_rows.get(course["course_id"]) or {}).get("programme_code") or "", auto=bool(course["auto_instalments"]))]
    for key in list(dict.fromkeys(body.addons))[:4]:
        extra_course = await service.course_by_key(db, key) if key != course["key"] else None
        if extra_course and extra_course["plan_type"] == "cpd" and addon_price(extra_course) > 0:
            lines.append(dict(course_id=extra_course["course_id"], title=extra_course["title"], plan_type="cpd", kind="addon",
                              amount=addon_price(extra_course), programme_code=(web_rows.get(extra_course["course_id"]) or {}).get("programme_code") or "",
                              auto=True))
    amount = sum((money(x["amount"]) for x in lines), money(0))
    if body.quoted_amount is not None and money(body.quoted_amount) != amount:
        raise ConflictError(f"The price has changed to LKR {amount:,.0f}. Please check and try again.")
    extra = {"free": [str(x)[:80] for x in body.free[:2]], **{k: v for k, v in (body.extra or {}).items() if k in ("utm", "start", "who", "referral", "scholarship")}}
    utm = extra.get("utm") if isinstance(extra.get("utm"), dict) else None
    lead_id = await leads.upsert(db, kind="order", name=body.name, email=body.email, phone=body.phone, course_id=course["course_id"],
                                 plan=terms["label"], programme_fee=float(course["fee"] or 0) or None,
                                 registration_fee=float(terms["today"]) if terms["code"] != "full" else None, utm=utm,
                                 referral=str(extra.get("referral") or "")[:40],
                                 extra_note=f"{course['title']} · {terms['label']} · LKR {amount:,.0f} by {'card' if body.method == 'payhere' else 'bank transfer'}")
    async with transaction(write=True) as c:
        order = await checkout.create_order(c, course=course, terms=terms, plan_label=terms["label"], lines=lines, method=body.method,
                                            name=body.name, email=body.email, phone=body.phone, crm_lead_id=lead_id, extra=extra)
    background.add_task(_send_offer, lead_id)
    out = {"reference": order["reference"], "amount": float(order["amount"]), "status": order["status"], "checkout": None,
           "pay_url": f"{settings.WEB_SITE_URL.rstrip('/')}/pay/{order['token']}", "token": order["token"]}
    if order["amount"] > 0 and body.method == "payhere":
        if checkout.card_ready():
            out["checkout"] = {"action": handoff_url(order["token"]), "fields": {}, "method": "get"}
        else:
            out["checkout_error"] = "Card payments are not available yet. Please pay by bank transfer."
    return out


@router.get("/orders/{reference}", dependencies=[Depends(require("WEB_CHECKOUT_ENABLED"))])
async def order_status(reference: str, request: Request):
    limit(request, "web-order-status", 60)
    async with transaction() as c:
        o = await c.fetchrow("SELECT reference, status, amount FROM web_orders WHERE reference=$1", reference.strip().upper()[:20])
    if not o:
        raise NotFoundError("Order not found")
    return {"reference": o["reference"], "status": o["status"], "amount": float(o["amount"])}


# ---------------------------------------------------------------- the student's invoice page
@router.get("/pay/{token}", dependencies=[Depends(require("WEB_CHECKOUT_ENABLED"))])
async def pay_view(token: str, request: Request):
    limit(request, "web-pay-view", 120)
    async with transaction() as c:
        return await checkout.pay_view(c, token)


@router.post("/pay/{token}/checkout", dependencies=[Depends(require("WEB_CHECKOUT_ENABLED"))])
async def pay_checkout(token: str, request: Request):
    limit(request, "web-pay-checkout", 20)
    if not checkout.card_ready():
        raise ConflictError("Card payments are not available yet. Please pay by bank transfer.")
    async with transaction() as c:
        await checkout.resolve(c, token)
    return {"action": handoff_url(token), "fields": {}, "method": "get"}


class SlipIn(BaseModel):
    file_name: str = Field("slip", max_length=200)
    content_type: str = Field("", max_length=100)
    data_base64: str = Field(..., max_length=6_000_000)
    amount: float
    paid_on: date | None = None
    reference: str = Field("", max_length=120)
    note: str = Field("", max_length=500)


@router.post("/pay/{token}/slip", dependencies=[Depends(require("WEB_SLIPS_ENABLED"))])
async def upload_slip(token: str, body: SlipIn, request: Request):
    limit(request, "web-slip", 8, 3600)
    if not 0 < body.amount <= 10_000_000:
        raise ValidationError("Please enter the amount you transferred.")
    data, mime, ext = checkout.decode_slip(body.data_base64)
    async with transaction() as c:
        await checkout.slip_target(c, token)  # fail fast before storing a file
    key = await checkout.store_slip_file(data, mime, ext)
    async with transaction(write=True) as c:
        target = await checkout.slip_target(c, token)
        await checkout.add_slip(c, target, key=key, file_name=body.file_name, mime=mime, size=len(data), amount=body.amount,
                                paid_on=body.paid_on.isoformat() if body.paid_on else None, reference=body.reference, note=body.note)
    lead_id = target["order"]["crm_lead_id"] if target["order"] else None
    if lead_id:
        try:
            async with AsyncSessionLocal() as db:
                await leads.activity(db, lead_id, f"Uploaded a bank slip for {target['ref']} (LKR {body.amount:,.2f}). Waiting for finance review.")
                await db.commit()
        except Exception:
            logger.exception("Slip note on lead %s failed", lead_id)
    return {"ok": True, "status": "pending"}


@handoff_router.get("/pay/web/{token}/go", response_class=HTMLResponse)
async def payhere_handoff(token: str, request: Request):
    """Builds the PayHere form on this domain (approved with PayHere) and submits it straight away."""
    limit(request, "web-handoff", 20)
    if not settings.WEB_CHECKOUT_ENABLED:
        raise APIError(503, "WEB_FEATURE_OFF", "Card payments are not switched on yet.")
    back = f"{settings.WEB_SITE_URL.rstrip('/')}/pay/{html.escape(token, quote=True)}"
    try:
        async with transaction(write=True) as c:
            form = await checkout.checkout(c, token)
    except APIError as exc:
        page = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Payment</title>"
                f"<body style='font-family:Arial,sans-serif;max-width:520px;margin:60px auto;padding:0 18px;color:#1d1530'>"
                f"<h2>We couldn't open the card payment</h2><p>{html.escape(exc.message)}</p><p><a href='{back}'>Back to your payment page</a></p></body>")
        return HTMLResponse(page, status_code=exc.status_code, headers=NO_STORE)
    inputs = "".join(f"<input type=hidden name='{html.escape(k, quote=True)}' value='{html.escape(str(v), quote=True)}'>" for k, v in form["fields"].items())
    page = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Opening PayHere…</title>"
            f"<body style='font-family:Arial,sans-serif;text-align:center;padding:70px 18px;color:#1d1530'>"
            f"<p>Opening the secure PayHere payment page…</p>"
            f"<form id=f method=post action='{html.escape(form['action'], quote=True)}'>{inputs}<button type=submit>Continue to PayHere</button></form>"
            f"<script>document.getElementById('f').submit()</script></body>")
    headers = {**NO_STORE, "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
               "form-action https://www.payhere.lk https://sandbox.payhere.lk; base-uri 'none'; frame-ancestors 'none'"}
    return HTMLResponse(page, headers=headers)


# ---------------------------------------------------------------- free demo LMS
class SignupIn(BaseModel):
    name: str = Field(..., max_length=200)
    email: str = Field(..., max_length=200)
    phone: str = Field(..., max_length=60)
    interest: str = Field("", max_length=200)
    password: str = Field(..., max_length=200)
    utm: dict | None = None


class LoginIn(BaseModel):
    email: str = Field(..., max_length=200)
    password: str = Field(..., max_length=200)


def _bearer(authorization: str) -> str:
    return authorization.removeprefix("Bearer ").strip()


@router.post("/demo/signup", dependencies=[Depends(require("WEB_DEMO_ENABLED"))])
async def demo_signup(body: SignupIn, request: Request, db: AsyncSession = Depends(get_db)):
    limit(request, "web-demo-signup", 6, 600)
    user, session = await demo.signup(db, name=body.name, email=body.email, phone=body.phone, interest=body.interest, password=body.password)
    try:
        lead_id = await leads.upsert(db, kind="demo", name=user["full_name"], email=user["email"], phone=user["phone"],
                                     utm=body.utm if isinstance(body.utm, dict) else None, extra_note=user["interest"])
        await db.execute(text("UPDATE web_demo_users SET crm_lead_id=:l WHERE id=:u"), {"l": lead_id, "u": user["id"]})
        await db.commit()
    except Exception:
        logger.exception("Demo signup lead failed for %s", user["id"])
        await db.rollback()
    return session


@router.post("/demo/login", dependencies=[Depends(require("WEB_DEMO_ENABLED"))])
async def demo_login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    limit(request, "web-demo-login", 20, 600)
    return await demo.login(db, body.email, body.password)


@router.post("/demo/reset-password", dependencies=[Depends(require("WEB_DEMO_ENABLED"))])
async def demo_reset(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    limit(request, "web-demo-reset", 10, 600)
    return await demo.reset_password(db, body.email, body.password)


@router.get("/demo/me", dependencies=[Depends(require("WEB_DEMO_ENABLED"))])
async def demo_me(authorization: str = Header(default=""), db: AsyncSession = Depends(get_db)):
    user = await demo.user_for_token(db, _bearer(authorization))
    state = await db.scalar(text("SELECT state FROM web_demo_states WHERE user_id=:u"), {"u": user["id"]})
    return {"user": {"name": user["full_name"], "email": user["email"], "phone": user["phone"], "interest": user["interest"]},
            "expires_at": user["expires_at"].isoformat(), "state": state or None, "classes": await demo.published_classes(db)}


@router.put("/demo/state", dependencies=[Depends(require("WEB_DEMO_ENABLED"))])
async def demo_state(request: Request, authorization: str = Header(default=""), db: AsyncSession = Depends(get_db)):
    user = await demo.user_for_token(db, _bearer(authorization))
    raw = await request.body()
    if len(raw) > demo.MAX_STATE_BYTES + 1000:
        raise APIError(413, "DEMO_STATE_TOO_LARGE", "Demo data is too large.")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError as exc:
        raise ValidationError("Invalid JSON") from exc
    state = payload.get("state") if isinstance(payload, dict) else None
    if not isinstance(state, dict):
        raise ValidationError("state must be an object")
    return {"ok": True, "events": await demo.save_state(db, user, state, len(raw))}


@router.get("/live-feed", dependencies=[Depends(require("WEB_DEMO_ENABLED"))])
async def live_feed(db: AsyncSession = Depends(get_db)):
    return await demo.live_feed(db)
