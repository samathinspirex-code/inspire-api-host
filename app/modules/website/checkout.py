"""Website enrolment orders and how they enter the Accounts ledger.

An order (payments.web_orders) is only a price snapshot until money arrives. On the first verified PayHere payment or an
approved bank slip, one transaction creates the learner, enrolment(s), invoice(s) and receipt(s). For monthly plans the
schedule (payments.instalments) starts when the registration invoice is paid: month 1 is due one month after that
payment, then the same day each month. From then on the existing Accounts daily job issues each instalment invoice
5 days before it is due, sends the payment link and the 3 / 0 / +7 day reminders.
"""
import base64
import logging
import secrets
import string
from datetime import datetime, timezone

import asyncpg

from app.core.config import settings
from app.core.errors import APIError, ConflictError, NotFoundError, ValidationError
from app.modules.payments import billing, core, payhere
from app.modules.payments.db import audit, one, rows
from . import templates as T
from .pricing import DEFAULT_PROGRAMME, money

logger = logging.getLogger(__name__)
ACTOR = "website"
MAX_SLIP_BYTES = 4 * 1024 * 1024
MAGIC = {b"\xff\xd8\xff": ("image/jpeg", ".jpg"), b"\x89PNG\r\n\x1a\n": ("image/png", ".png"), b"%PDF-": ("application/pdf", ".pdf")}


def new_reference() -> str:
    return "IC-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))


def first_name(name: str) -> str:
    return (name or "there").strip().split(" ")[0] or "there"


def digits9(phone) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())[-9:]


async def web_settings(c) -> dict:
    s = await billing.settings(c)
    base = core.DEFAULTS["web_checkout"]
    stored = s.get("web_checkout") or {}
    return {**base, **stored, "bank": {**base["bank"], **(stored.get("bank") or {})}, "year2": {**base["year2"], **(stored.get("year2") or {})}}


def card_ready() -> bool:
    return bool(settings.WEB_CHECKOUT_ENABLED and settings.PAYMENTS_PAYHERE_MERCHANT_ID and settings.PAYMENTS_PAYHERE_MERCHANT_SECRET
                and settings.PAYMENTS_PAYHERE_MODE in {"sandbox", "live"})


async def queue_email(c, to: str, subject: str, html: str, text: str, link_id=None):
    """Website emails go through the Accounts outbox (sent by the delivery worker). A test inbox catches all of them."""
    to = (settings.PAYMENTS_MAIL_TEST_TO or to or "").strip()
    if not to:
        return None
    return await c.fetchval("""INSERT INTO message_outbox(channel,to_addr,subject,body,payment_link_id,html_body)
        VALUES('email',$1,$2,$3,$4,$5) RETURNING id""", to, subject[:300], text, link_id, html)


async def received_date(c, wanted: str | None) -> str:
    """The payment date, or today if that month is already locked by month-end close."""
    d = core.day(wanted or core.today())
    locked = await c.fetchval("SELECT status='locked' FROM fiscal_periods WHERE period_start=$1", core.month_start(d))
    return core.today() if locked or d > core.today() else d


# ---------------------------------------------------------------- orders
async def create_order(c, *, course: dict, terms: dict, plan_label: str, lines: list[dict], method: str, name: str, email: str,
                       phone: str, crm_lead_id: int | None, extra: dict) -> dict:
    amount = sum((money(line["amount"]) for line in lines), money(0))
    for _ in range(5):
        ref = new_reference()
        if not await c.fetchval("SELECT 1 FROM web_orders WHERE reference=$1", ref):
            break
    is_test = name.strip().upper().startswith("ZZ TEST")
    r = await one(c, """INSERT INTO web_orders(reference,token,status,method,full_name,email,phone,course_id,course_title,plan_code,plan_label,
            plan_type,terms,lines,amount,crm_lead_id,is_test,extra)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18) RETURNING *""",
                  ref, secrets.token_urlsafe(24), "pending" if amount > 0 else "reserved", method, name.strip()[:200], email.strip().lower()[:200],
                  phone.strip()[:50], course["course_id"], course["title"][:300], terms["code"], plan_label[:200], course["plan_type"],
                  terms, lines, amount, crm_lead_id, is_test, extra)
    if amount > 0 and method == "bank_transfer":
        cfg = await web_settings(c)
        subject, html, text = T.order_created(first_name(name), ref, course["title"], amount, r["token"], cfg["bank"])
        await queue_email(c, r["email"], subject, html, text)
    await audit(c, ACTOR, "create", "web_order", r["id"], dict(reference=ref, amount=str(amount), plan=terms["code"]))
    return r


async def _programme(c, code: str | None, plan_type: str | None) -> str:
    for candidate in (code, DEFAULT_PROGRAMME.get(plan_type or ""), "CPD"):
        if candidate and await c.fetchval("SELECT 1 FROM programmes WHERE code=$1 AND active", candidate):
            return candidate
    fallback = await c.fetchval("SELECT code FROM programmes WHERE active ORDER BY code LIMIT 1")
    if not fallback:
        raise ConflictError("Add an active programme in Accounts before taking website payments")
    return fallback


async def _learner(c, order: dict) -> tuple[dict, bool]:
    """Reuse a learner only when both email and phone agree; otherwise create one and flag a possible duplicate."""
    same_email = await rows(c, "SELECT * FROM learners WHERE lower(email)=lower($1) ORDER BY created_at", order["email"])
    for learner in same_email:
        if not learner["phone"] or digits9(learner["phone"]) == digits9(order["phone"]):
            return learner, False
    phone_taken = await c.fetchval("""SELECT 1 FROM learners WHERE right(regexp_replace(coalesce(phone,''),'\\D','','g'),9)=$1 LIMIT 1""",
                                   digits9(order["phone"]))
    parts = order["full_name"].split()
    learner = await billing.create_learner(c, dict(first_name=parts[0] if parts else "Student", last_name=" ".join(parts[1:]),
                                                  email=order["email"], phone=order["phone"]), ACTOR)
    return learner, bool(same_email or phone_taken)


async def activate(c, enrolment_id, paid_on: str) -> None:
    """Create the monthly schedule once the registration invoice is paid (month 1 = one month after payment)."""
    we = await one(c, "SELECT * FROM web_enrolments WHERE enrolment_id=$1 FOR UPDATE", enrolment_id)
    if not we or we["activated_at"]:
        return
    n, amount = int(we["instalment_count"] or 0), money(we["instalment_amount"])
    if we["plan_code"] == "monthly" and we["auto"] and n > 0 and amount > 0:
        if not await c.fetchval("SELECT 1 FROM instalments WHERE enrolment_id=$1", enrolment_id):
            for i in range(1, n + 1):
                await c.execute("INSERT INTO instalments(enrolment_id,seq,due_date,amount) VALUES($1,$2,$3,$4)",
                                enrolment_id, i, core.add_months(paid_on, i), amount)
            await c.execute("UPDATE enrolments SET start_date=$2 WHERE id=$1", enrolment_id, paid_on)
    await c.execute("UPDATE web_enrolments SET activated_at=now() WHERE enrolment_id=$1", enrolment_id)


async def after_receipt(c, invoice_id, received: str) -> None:
    """Called by Accounts after every receipt: starts website schedules and keeps website links at the remaining balance."""
    inv = await one(c, "SELECT * FROM invoices WHERE id=$1", invoice_id)
    if not inv:
        return
    if inv["status"] == "paid":
        we = await one(c, "SELECT enrolment_id FROM web_enrolments WHERE activation_invoice_id=$1", invoice_id)
        if we:
            await activate(c, we["enrolment_id"], received)
    elif inv["status"] == "open" and inv["amount"] > inv["amount_paid"]:
        await c.execute("UPDATE payment_links SET amount=$2 WHERE invoice_id=$1 AND status='sent' AND origin='website'",
                        invoice_id, inv["amount"] - inv["amount_paid"])


async def materialise(c, order: dict, *, method: str, amount, received: str, gateway_id: str | None = None, actor: str = ACTOR) -> dict:
    """First money for an order: learner, enrolment(s), invoice(s), receipt(s). Caller holds the write transaction."""
    if order["learner_id"] and order["status"] == "paid":
        raise ConflictError("This order is already recorded in Accounts")
    amount = money(amount)
    learner, duplicate = await _learner(c, order)
    terms = order["terms"] or {}
    invoices, main_enrolment = [], None
    for line in order["lines"]:
        main = line.get("kind") == "main"
        code = order["plan_code"] if main else "full"
        line_amount = money(line.get("amount"))
        n = int(terms.get("instalments") or 0) if main and code == "monthly" else 0
        per = money(terms.get("instalment_amount")) if n else money(0)
        auto = bool(line.get("auto", True))
        total, count = (per * n, n) if (code == "monthly" and auto and n) else ((line_amount, 1) if code == "full" else (money(0), 1))
        programme = await _programme(c, line.get("programme_code"), line.get("plan_type"))
        e = await one(c, """INSERT INTO enrolments(learner_id,programme_code,start_date,total_fee,instalments,counsellor)
            VALUES($1,$2,$3,$4,$5,'New website') RETURNING *""", learner["id"], programme, received, total, max(count, 1))
        inv = None
        if code == "full" and line_amount > 0:
            it = await one(c, "INSERT INTO instalments(enrolment_id,seq,due_date,amount) VALUES($1,1,$2,$3) RETURNING *", e["id"], received, line_amount)
            inv = await billing.invoice(c, dict(learner_id=learner["id"], enrolment_id=e["id"], instalment_id=it["id"], kind="course",
                                                description="Course fee — " + line["title"], issue_date=received, due_date=received,
                                                amount=line_amount), actor)
        elif line_amount > 0:
            inv = await billing.invoice(c, dict(learner_id=learner["id"], enrolment_id=e["id"], kind="registration",
                                                description="Registration fee — " + line["title"], issue_date=received, due_date=received,
                                                amount=line_amount), actor)
        await c.execute("""INSERT INTO web_enrolments(enrolment_id,web_order_id,course_id,plan_code,plan_label,year,activation_invoice_id,auto,
                instalment_count,instalment_amount) VALUES($1,$2,$3,$4,$5,1,$6,$7,$8,$9)""",
                        e["id"], order["id"], line.get("course_id"), code, (order["plan_label"] if main else "Add-on · pay in full"),
                        inv["id"] if inv else None, auto, n, per)
        if main:
            main_enrolment = e
        if inv:
            invoices.append(inv)
        elif code != "monthly":
            await c.execute("UPDATE web_enrolments SET activated_at=now() WHERE enrolment_id=$1", e["id"])
    remaining, payment_ids = amount, []
    for idx, inv in enumerate(invoices):
        if remaining <= 0:
            break
        part = remaining if idx == len(invoices) - 1 else min(remaining, inv["amount"])
        gid = (gateway_id if idx == 0 else f"{gateway_id}:{idx + 1}") if gateway_id else None
        r = await billing.receipt(c, dict(invoice_id=inv["id"], amount=part, method=method, gateway_payment_id=gid, received_date=received,
                                          memo=("PayHere " + gateway_id if gateway_id else "Bank slip") + " — " + order["reference"], quiet=True), actor)
        payment_ids.append(r["payment"]["id"])
        remaining -= part
    if remaining > 0 and not invoices:
        r = await billing.receipt(c, dict(learner_id=learner["id"], amount=remaining, method=method, gateway_payment_id=gateway_id,
                                          received_date=received, memo="Website advance — " + order["reference"], quiet=True), actor)
        payment_ids.append(r["payment"]["id"])
    status = "paid" if amount > 0 else order["status"]
    await c.execute("""UPDATE web_orders SET status=$2, learner_id=$3, possible_duplicate=$4, paid_at=CASE WHEN $5 THEN now() ELSE paid_at END,
        updated_at=now() WHERE id=$1""", order["id"], status, learner["id"], duplicate, amount > 0)
    await audit(c, actor, "materialise", "web_order", order["id"], dict(learner=learner["learner_no"], amount=str(amount), method=method))
    if amount > 0:
        view = await ledger_view(c, order["token"], main_enrolment, None)
        subject, html, text = T.receipt(first_name(order["full_name"]), amount, method, order["course_title"],
                                        [i["number"] for i in invoices], order["token"], view["schedule"], view.get("_next"))
        await queue_email(c, order["email"], subject, html, text)
    return dict(learner_no=learner["learner_no"], learner_id=learner["id"], invoices=[i["number"] for i in invoices],
                payment_ids=payment_ids, possible_duplicate=duplicate)


# ---------------------------------------------------------------- the student's page (/pay/<token>)
def _label(row: dict, as_of: str) -> str:
    if row["status"] == "paid":
        return "Paid"
    if row["status"] in ("open", "invoiced"):
        return "Overdue" if str(row["due_date"]) < as_of else "Due"
    if row["status"] == "cancelled":
        return "Cancelled"
    return "Upcoming"


async def _schedule(c, enrolment_id, as_of: str) -> tuple[list[dict], dict | None]:
    if not enrolment_id:
        return [], None
    we = await one(c, "SELECT * FROM web_enrolments WHERE enrolment_id=$1", enrolment_id) or {}
    year2 = "Year 2 · " if we.get("year") == 2 else ""
    out = []
    regs = await rows(c, "SELECT * FROM invoices WHERE enrolment_id=$1 AND kind='registration' AND status<>'cancelled' ORDER BY issue_date", enrolment_id)
    for inv in regs:
        out.append(dict(label="Registration fee", due_date=inv["due_date"], amount=inv["amount"], status=inv["status"], status_label=_label(inv, as_of)))
    its = await rows(c, """SELECT i.*, v.status AS invoice_status, v.amount_paid FROM instalments i LEFT JOIN invoices v ON v.id=i.invoice_id
        WHERE i.enrolment_id=$1 AND i.status<>'cancelled' ORDER BY i.seq""", enrolment_id)
    n = len(its)
    for it in its:
        label = "Full payment" if n == 1 and we.get("plan_code") == "full" else f"{year2}Month {it['seq']} of {n}"
        out.append(dict(label=label, due_date=it["due_date"], amount=it["amount"], status=it["status"], status_label=_label(it, as_of)))
    nxt = next((s for s in out if s["status"] in ("scheduled", "invoiced", "open")), None)
    return out, nxt


async def _children(c, enrolment_id) -> list:
    return [r["enrolment_id"] for r in await rows(c, "SELECT enrolment_id FROM web_enrolments WHERE parent_enrolment_id=$1", enrolment_id)]


async def ledger_view(c, token: str, enrolment: dict | None, invoice: dict | None) -> dict:
    as_of = core.today()
    schedule, nxt = await _schedule(c, enrolment["id"] if enrolment else None, as_of)
    if enrolment:
        for child in await _children(c, enrolment["id"]):
            more, more_next = await _schedule(c, child, as_of)
            schedule += more
            nxt = nxt or more_next
    paid_total = await c.fetchval("SELECT COALESCE(SUM(amount_paid),0) FROM invoices WHERE enrolment_id=$1", enrolment["id"]) if enrolment else money(0)
    instalments = [s for s in schedule if s["label"].startswith(("Month", "Year 2", "Full"))]
    summary = dict(instalments=len(instalments), instalments_paid=sum(1 for s in instalments if s["status"] == "paid"), paid_total=paid_total,
                   balance=sum((s["amount"] for s in schedule if s["status"] != "paid"), money(0)),
                   next_due=nxt["due_date"] if nxt else None, next_amount=nxt["amount"] if nxt else None)
    return dict(schedule=schedule, summary=summary, _next=nxt)


async def _open_invoice_for_order(c, order: dict) -> dict | None:
    return await one(c, """SELECT v.* FROM invoices v JOIN web_enrolments w ON w.enrolment_id=v.enrolment_id
        WHERE (w.web_order_id=$1 OR w.parent_enrolment_id IN (SELECT enrolment_id FROM web_enrolments WHERE web_order_id=$1))
          AND v.status='open' ORDER BY v.due_date, v.created_at LIMIT 1""", order["id"])


async def _main_enrolment(c, order: dict) -> dict | None:
    return await one(c, """SELECT e.* FROM enrolments e JOIN web_enrolments w ON w.enrolment_id=e.id
        WHERE w.web_order_id=$1 AND w.year=1 ORDER BY (w.plan_label <> 'Add-on · pay in full') DESC, e.created_at LIMIT 1""", order["id"])


async def _latest_slip(c, *, order_id=None, invoice_id=None) -> dict | None:
    s = await one(c, """SELECT status, reason, created_at FROM bank_slips WHERE ($1::uuid IS NOT NULL AND web_order_id=$1)
        OR ($2::uuid IS NOT NULL AND invoice_id=$2) ORDER BY created_at DESC LIMIT 1""", order_id, invoice_id)
    return dict(status=s["status"], reason=s["reason"], uploaded=s["created_at"]) if s else None


def _invoice_block(number, description, amount, paid, due, status, status_label, lines) -> dict:
    return dict(number=number, description=description, amount=amount, paid=paid, balance=max(money(amount) - money(paid), money(0)),
                due_date=due, status=status, status_label=status_label, lines=lines)


async def resolve(c, token: str) -> tuple[str, dict]:
    if not 16 <= len(token or "") <= 80:
        raise NotFoundError("This payment link is not valid. Please contact us.")
    o = await one(c, "SELECT * FROM web_orders WHERE token=$1", token)
    if o:
        if o["status"] == "cancelled":
            raise NotFoundError("This order was cancelled. Please contact us.")
        return "order", o
    link = await one(c, "SELECT * FROM payment_links WHERE token=$1", token)
    if link and link["status"] != "cancelled":
        return "link", link
    raise NotFoundError("This payment link is not valid. Please contact us.")


async def pay_view(c, token: str) -> dict:
    kind, rec = await resolve(c, token)
    cfg = await web_settings(c)
    if kind == "order" and not rec["learner_id"]:
        o, as_of = rec, core.today()
        terms = o["terms"] or {}
        lines = [dict(label=("Registration fee — " if o["plan_code"] != "full" and line.get("kind") == "main" else "") + line["title"],
                      amount=line["amount"]) for line in o["lines"]]
        schedule = [dict(label="Due today", due_date=o["created_at"].date().isoformat(), amount=o["amount"], status="open", status_label="Due")]
        if o["plan_code"] == "monthly" and terms.get("instalments"):
            for i in range(1, int(terms["instalments"]) + 1):
                schedule.append(dict(label=f"Month {i} of {terms['instalments']}", due_date=core.add_months(as_of, i), amount=terms["instalment_amount"],
                                     status="scheduled", status_label="Upcoming"))
        label = {"pending": "Due", "expired": "Due", "reserved": "Reserved", "paid": "Paid"}.get(o["status"], "Due")
        return dict(invoice=_invoice_block(o["reference"], f"{o['course_title']} — {o['plan_label']}".strip(" —"), o["amount"], 0,
                                           o["created_at"].date().isoformat(), "open" if o["amount"] > 0 else "reserved", label, lines),
                    student=dict(first_name=first_name(o["full_name"])), course=o["course_title"], plan=o["plan_label"], schedule=schedule,
                    summary=None, bank=cfg["bank"], card_enabled=card_ready() and o["amount"] > 0, slip=await _latest_slip(c, order_id=o["id"]),
                    reference=o["reference"])
    if kind == "order":
        o = rec
        enrolment = await _main_enrolment(c, o)
        inv = await _open_invoice_for_order(c, o) or await one(c, """SELECT v.* FROM invoices v JOIN web_enrolments w ON w.activation_invoice_id=v.id
            WHERE w.web_order_id=$1 ORDER BY v.created_at LIMIT 1""", o["id"])
        name, course, plan = o["full_name"], o["course_title"], o["plan_label"]
    else:
        inv = await one(c, "SELECT * FROM invoices WHERE id=$1", rec["invoice_id"])
        enrolment = await one(c, "SELECT * FROM enrolments WHERE id=$1", inv["enrolment_id"]) if inv and inv["enrolment_id"] else None
        we = await one(c, "SELECT * FROM web_enrolments WHERE enrolment_id=$1", enrolment["id"]) if enrolment else None
        if we and we["parent_enrolment_id"]:
            enrolment = await one(c, "SELECT * FROM enrolments WHERE id=$1", we["parent_enrolment_id"]) or enrolment
        learner = await one(c, "SELECT * FROM learners WHERE id=$1", rec["learner_id"])
        prog = await c.fetchval("SELECT name FROM programmes WHERE code=$1", enrolment["programme_code"]) if enrolment else ""
        o = await one(c, """SELECT o.* FROM web_orders o JOIN web_enrolments w ON w.web_order_id=o.id WHERE w.enrolment_id=$1""", enrolment["id"]) if enrolment else None
        name = learner["first_name"]
        course, plan = (o["course_title"], o["plan_label"]) if o else (prog or rec["purpose"], "")
    view = await ledger_view(c, token, enrolment, inv)
    as_of = core.today()
    block = _invoice_block(inv["number"], inv["description"], inv["amount"], inv["amount_paid"], inv["due_date"], inv["status"],
                           _label(inv, as_of), [dict(label=inv["description"], amount=inv["amount"])]) if inv else \
        _invoice_block(rec.get("reference", ""), course, 0, 0, None, "paid", "Paid", [])
    return dict(invoice=block, student=dict(first_name=first_name(name)), course=course, plan=plan, schedule=view["schedule"],
                summary=view["summary"], bank=cfg["bank"], card_enabled=card_ready() and bool(inv and inv["status"] == "open"),
                slip=await _latest_slip(c, invoice_id=inv["id"]) if inv else None, reference=o["reference"] if o else (inv["number"] if inv else ""))


async def _ensure_link(c, inv: dict) -> dict:
    link = await one(c, "SELECT * FROM payment_links WHERE invoice_id=$1 AND status='sent' FOR UPDATE", inv["id"])
    if link:
        return link
    n = await c.fetchval("SELECT nextval('payment_link_seq')")
    return await one(c, """INSERT INTO payment_links(code,token,invoice_id,learner_id,amount,purpose,channel,due_date,expires_at,created_by,origin)
        VALUES($1,$2,$3,$4,$5,$6,'email',$7,NULL,$8,'website') RETURNING *""", f"PL-{n}", secrets.token_urlsafe(24), inv["id"], inv["learner_id"],
                     inv["amount"] - inv["amount_paid"], inv["description"].split(" — ")[0], inv["due_date"], ACTOR)


async def checkout(c, token: str) -> dict:
    """PayHere form fields for a website order or an open website invoice."""
    if not card_ready():
        raise ConflictError("Card payments are not available yet. Please pay by bank transfer.")
    kind, rec = await resolve(c, token)
    if kind == "order" and not rec["learner_id"]:
        o = await one(c, "SELECT * FROM web_orders WHERE id=$1 FOR UPDATE", rec["id"])
        if o["status"] not in ("pending", "expired") or o["amount"] <= 0:
            raise ConflictError("This order has nothing to pay by card")
        attempt = await c.fetchval("SELECT COUNT(*) FROM web_checkouts WHERE web_order_id=$1", o["id"]) + 1
        order_id = f"WEB-{o['reference']}-{attempt}"
        await c.execute("INSERT INTO web_checkouts(order_id,web_order_id,amount) VALUES($1,$2,$3)", order_id, o["id"], o["amount"])
        parts = o["full_name"].split()
        site = settings.WEB_SITE_URL.rstrip("/")
        api = settings.PAYMENTS_PUBLIC_BASE_URL.rstrip("/")
        if settings.PAYMENTS_PAYHERE_MODE == "live" and not api.startswith("https://"):
            raise ConflictError("Live checkout requires a public HTTPS address")
        fields = dict(merchant_id=settings.PAYMENTS_PAYHERE_MERCHANT_ID, return_url=f"{site}/pay/{token}?status=return",
                      cancel_url=f"{site}/pay/{token}?status=cancelled", notify_url=api + "/api/v1/payments/payhere/notify", order_id=order_id,
                      items=("Inspire College — " + o["course_title"])[:250], currency="LKR", amount=f"{o['amount']:.2f}",
                      first_name=parts[0] if parts else "Student", last_name=" ".join(parts[1:]) or "-", email=o["email"], phone=o["phone"],
                      address="Colombo", city="Colombo", country="Sri Lanka", custom_1=o["reference"], custom_2="",
                      hash=payhere.checkout_hash(order_id, o["amount"]))
        return dict(action="https://www.payhere.lk/pay/checkout" if settings.PAYMENTS_PAYHERE_MODE == "live" else "https://sandbox.payhere.lk/pay/checkout",
                    fields=fields)
    if kind == "order":
        inv = await _open_invoice_for_order(c, rec)
        if not inv:
            raise ConflictError("Nothing is due right now")
        link = await _ensure_link(c, inv)
    else:
        link = rec
    return await payhere.checkout(c, link["token"])


async def notify(c, body: dict, amount, payment_id: str, log) -> dict:
    """PayHere callback for a website order (order_id WEB-...). Signature and duplicates are already checked."""
    wc = await one(c, "SELECT * FROM web_checkouts WHERE order_id=$1", body.get("order_id", ""))
    if not wc:
        return await log("rejected", "unknown website order")
    o = await one(c, "SELECT * FROM web_orders WHERE id=$1 FOR UPDATE", wc["web_order_id"])
    if body.get("payhere_currency") != "LKR" or amount != wc["amount"] or amount != o["amount"]:
        return await log("rejected", "amount or currency mismatch")
    if o["status"] == "paid" or o["learner_id"]:
        return await log("rejected", "website order already paid; review captured funds manually")
    if o["status"] == "cancelled":
        return await log("rejected", "website order cancelled; review captured funds manually")
    try:
        async with c.transaction():
            result = await materialise(c, o, method="payhere", amount=amount, received=await received_date(c, None),
                                       gateway_id=payment_id, actor="payhere")
    except (APIError, asyncpg.PostgresError) as exc:
        logger.exception("Website order %s could not be recorded", o["reference"])
        return await log("rejected", "could not record in Accounts (" + type(exc).__name__ + "); review captured funds manually")
    await log("posted", "verified website payment")
    return dict(ok=True, web_order=o["reference"], learner=result["learner_no"])


async def link_receipt(c, link: dict, amount, method: str) -> None:
    """Branded receipt (with the schedule) after a card payment on a website invoice link."""
    learner = await one(c, "SELECT * FROM learners WHERE id=$1", link["learner_id"])
    inv = await one(c, "SELECT * FROM invoices WHERE id=$1", link["invoice_id"])
    if not learner or not learner["email"] or not inv:
        return
    enrolment = await one(c, "SELECT * FROM enrolments WHERE id=$1", inv["enrolment_id"]) if inv["enrolment_id"] else None
    we = await one(c, "SELECT * FROM web_enrolments WHERE enrolment_id=$1", enrolment["id"]) if enrolment else None
    if we and we["parent_enrolment_id"]:
        enrolment = await one(c, "SELECT * FROM enrolments WHERE id=$1", we["parent_enrolment_id"]) or enrolment
    view = await ledger_view(c, link["token"], enrolment, inv)
    subject, html, text = T.receipt(learner["first_name"], amount, method, inv["description"], [inv["number"]], link["token"],
                                    view["schedule"], view.get("_next"))
    await queue_email(c, learner["email"], subject, html, text, link["id"])


# ---------------------------------------------------------------- bank slips
def sniff(head: bytes):
    for sig, kind in MAGIC.items():
        if head.startswith(sig):
            return kind
    return None


def _bucket() -> str:
    return settings.PAYMENTS_SLIP_BUCKET or settings.MEDIA_BUCKET


async def store_slip_file(data: bytes, mime: str, ext: str) -> str:
    import asyncio
    from uuid import uuid4
    from app.modules.cms import media_service
    key = f"payments/slips/{datetime.now(timezone.utc):%Y%m}/{uuid4().hex}{ext}"
    if not _bucket() and settings.PAYMENTS_SLIP_LOCAL_DIR:
        from pathlib import Path
        target = Path(settings.PAYMENTS_SLIP_LOCAL_DIR) / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return "local:" + key
    if not _bucket():
        raise ValidationError("Slip upload is not set up yet. Please send your slip to us on WhatsApp.")
    try:
        await asyncio.to_thread(media_service._client().put_object, Bucket=_bucket(), Key=key, Body=data, ContentType=mime,
                                ServerSideEncryption="AES256")
    except Exception as exc:
        raise ValidationError("The slip could not be stored. Please try again.") from exc
    return key


async def read_slip_file(key: str) -> bytes:
    import asyncio
    from app.modules.cms import media_service
    if key.startswith("local:"):
        from pathlib import Path
        return (Path(settings.PAYMENTS_SLIP_LOCAL_DIR) / key[6:]).read_bytes()
    obj = await asyncio.to_thread(media_service._client().get_object, Bucket=_bucket(), Key=key)
    return await asyncio.to_thread(obj["Body"].read)


def decode_slip(data_base64: str) -> tuple[bytes, str, str]:
    try:
        data = base64.b64decode(data_base64 or "", validate=True)
    except Exception as exc:
        raise ValidationError("The file could not be read") from exc
    if not data:
        raise ValidationError("Choose a file")
    if len(data) > MAX_SLIP_BYTES:
        raise ValidationError("The file is too large (max 4 MB)")
    kind = sniff(data[:16])
    if not kind:
        raise ValidationError("Please upload a photo (JPG or PNG) or a PDF of your slip")
    return data, kind[0], kind[1]


async def slip_target(c, token: str) -> dict:
    """What a slip on this page pays: the order (before any payment) or the open invoice."""
    kind, rec = await resolve(c, token)
    if kind == "order" and not rec["learner_id"]:
        if rec["status"] not in ("pending", "expired") or rec["amount"] <= 0:
            raise ConflictError("This order has nothing to pay")
        return dict(order=rec, invoice=None, name=rec["full_name"], email=rec["email"], ref=rec["reference"], what=rec["course_title"])
    inv = await _open_invoice_for_order(c, rec) if kind == "order" else await one(c, "SELECT * FROM invoices WHERE id=$1", rec["invoice_id"])
    if not inv or inv["status"] != "open":
        raise ConflictError("This invoice is already paid")
    learner = await one(c, "SELECT * FROM learners WHERE id=$1", inv["learner_id"])
    return dict(order=None, invoice=inv, name=learner["first_name"] + " " + learner["last_name"], email=learner["email"] or "",
                ref=inv["number"], what=inv["description"])


async def add_slip(c, target: dict, *, key: str, file_name: str, mime: str, size: int, amount, paid_on, reference: str, note: str) -> dict:
    order, inv = target["order"], target["invoice"]
    waiting = await c.fetchval("""SELECT COUNT(*) FROM bank_slips WHERE status='pending' AND (($1::uuid IS NOT NULL AND web_order_id=$1)
        OR ($2::uuid IS NOT NULL AND invoice_id=$2))""", order["id"] if order else None, inv["id"] if inv else None)
    if waiting >= 3:
        raise ConflictError("We already have slips waiting for review on this payment")
    s = await one(c, """INSERT INTO bank_slips(web_order_id,invoice_id,storage_key,file_name,mime,size_bytes,amount,paid_on,reference,note)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *""", order["id"] if order else None, inv["id"] if inv else None, key,
                  (file_name or "slip")[:200], mime, size, money(amount), paid_on, (reference or "").strip()[:120], (note or "").strip()[:500])
    subject, html, text = T.slip_received(first_name(target["name"]), target["ref"], target["what"])
    await queue_email(c, target["email"], subject, html, text)
    finance = (await web_settings(c)).get("finance_email") or ""
    if finance:
        subject, html, text = T.finance_slip_alert(target["name"], target["ref"], target["what"], amount, s["reference"])
        await queue_email(c, finance, subject, html, text)
    await audit(c, ACTOR, "upload", "bank_slip", s["id"], dict(ref=target["ref"], amount=str(s["amount"])))
    return s


async def approve_slip(c, slip_id, actor: str) -> dict:
    s = await one(c, "SELECT * FROM bank_slips WHERE id=$1 FOR UPDATE", slip_id)
    if not s:
        raise NotFoundError("Slip not found")
    if s["status"] != "pending":
        raise ConflictError("This slip was already " + s["status"])
    received = await received_date(c, s["paid_on"])
    if s["web_order_id"]:
        o = await one(c, "SELECT * FROM web_orders WHERE id=$1 FOR UPDATE", s["web_order_id"])
        if o["learner_id"] and o["status"] == "paid":
            raise ConflictError("This order is already paid. Record the slip on the learner in Learners & fees instead.")
        result = await materialise(c, o, method="bank_transfer", amount=s["amount"], received=received, actor=actor)
        payment_id = result["payment_ids"][0] if result["payment_ids"] else None
    else:
        inv = await one(c, "SELECT * FROM invoices WHERE id=$1", s["invoice_id"])
        if inv["status"] != "open":
            raise ConflictError("This invoice is already " + inv["status"])
        r = await billing.receipt(c, dict(invoice_id=inv["id"], amount=s["amount"], method="bank_transfer", received_date=received,
                                          memo="Bank slip — " + inv["number"], quiet=True), actor)
        payment_id = r["payment"]["id"]
        learner = await one(c, "SELECT * FROM learners WHERE id=$1", inv["learner_id"])
        link = await _ensure_link(c, inv) if (await one(c, "SELECT status FROM invoices WHERE id=$1", inv["id"]))["status"] == "open" else \
            await one(c, "SELECT * FROM payment_links WHERE invoice_id=$1 ORDER BY created_at DESC LIMIT 1", inv["id"])
        enrolment = await one(c, "SELECT * FROM enrolments WHERE id=$1", inv["enrolment_id"]) if inv["enrolment_id"] else None
        view = await ledger_view(c, link["token"] if link else "", enrolment, inv)
        if learner and learner["email"] and link:
            subject, html, text = T.receipt(learner["first_name"], s["amount"], "bank_transfer", inv["description"], [inv["number"]],
                                            link["token"], view["schedule"], view.get("_next"))
            await queue_email(c, learner["email"], subject, html, text)
        result = dict(invoices=[inv["number"]])
    await c.execute("UPDATE bank_slips SET status='approved', reviewed_by=$2, reviewed_at=now(), payment_id=$3 WHERE id=$1", slip_id, actor, payment_id)
    await audit(c, actor, "approve", "bank_slip", slip_id, dict(received=received))
    return dict(ok=True, received_date=received, **{k: v for k, v in result.items() if k in ("learner_no", "invoices", "possible_duplicate")})


async def reject_slip(c, slip_id, reason: str, actor: str) -> dict:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationError("Tell the student why the slip was rejected")
    s = await one(c, "SELECT * FROM bank_slips WHERE id=$1 FOR UPDATE", slip_id)
    if not s:
        raise NotFoundError("Slip not found")
    if s["status"] != "pending":
        raise ConflictError("This slip was already " + s["status"])
    await c.execute("UPDATE bank_slips SET status='rejected', reason=$2, reviewed_by=$3, reviewed_at=now() WHERE id=$1", slip_id, reason[:500], actor)
    if s["web_order_id"]:
        o = await one(c, "SELECT * FROM web_orders WHERE id=$1", s["web_order_id"])
        name, email, ref, what, token = o["full_name"], o["email"], o["reference"], o["course_title"], o["token"]
    else:
        inv = await one(c, "SELECT * FROM invoices WHERE id=$1", s["invoice_id"])
        learner = await one(c, "SELECT * FROM learners WHERE id=$1", inv["learner_id"])
        link = await _ensure_link(c, inv) if inv["status"] == "open" else None
        name, email, ref, what, token = learner["first_name"], learner["email"] or "", inv["number"], inv["description"], link["token"] if link else ""
    if token:
        subject, html, text = T.slip_rejected(first_name(name), ref, what, reason, token)
        await queue_email(c, email, subject, html, text)
    await audit(c, actor, "reject", "bank_slip", slip_id, dict(reason=reason[:200]))
    return dict(ok=True)


# ---------------------------------------------------------------- finance actions
async def set_plan(c, enrolment_id, *, plan_code: str, instalments: int, amount, first_due: str, actor: str) -> dict:
    """Finance sets the plan for a reserved / advisor / automatic-off enrolment: creates its schedule."""
    e = await one(c, "SELECT * FROM enrolments WHERE id=$1 FOR UPDATE", enrolment_id)
    we = await one(c, "SELECT * FROM web_enrolments WHERE enrolment_id=$1 FOR UPDATE", enrolment_id)
    if not e or not we:
        raise NotFoundError("Website enrolment not found")
    if e["status"] != "active":
        raise ConflictError("Enrolment is not active")
    if await c.fetchval("SELECT 1 FROM instalments WHERE enrolment_id=$1 AND status<>'cancelled'", enrolment_id):
        raise ConflictError("This enrolment already has a schedule")
    if plan_code not in ("monthly", "full"):
        raise ValidationError("Choose monthly or pay in full")
    n = int(instalments) if plan_code == "monthly" else 1
    if not 1 <= n <= 36:
        raise ValidationError("Use 1 to 36 instalments")
    per = core.money(amount, True)
    first = core.day(first_due)
    for i in range(1, n + 1):
        await c.execute("INSERT INTO instalments(enrolment_id,seq,due_date,amount) VALUES($1,$2,$3,$4)", enrolment_id, i, core.add_months(first, i - 1), per)
    await c.execute("UPDATE enrolments SET total_fee=$2, instalments=$3 WHERE id=$1", enrolment_id, per * n, n)
    await c.execute("""UPDATE web_enrolments SET plan_code=$2, instalment_count=$3, instalment_amount=$4, activated_at=COALESCE(activated_at, now())
        WHERE enrolment_id=$1""", enrolment_id, plan_code, n, per)
    await audit(c, actor, "set_plan", "enrolment", enrolment_id, dict(plan=plan_code, instalments=n, amount=str(per), first_due=first))
    return dict(ok=True, instalments=n, total=per * n)


async def start_year2(c, enrolment_id, *, instalments: int, amount, first_due: str, actor: str) -> dict:
    e = await one(c, "SELECT * FROM enrolments WHERE id=$1", enrolment_id)
    we = await one(c, "SELECT * FROM web_enrolments WHERE enrolment_id=$1", enrolment_id)
    if not e or not we or we["year"] != 1:
        raise NotFoundError("Year 1 website enrolment not found")
    if await c.fetchval("SELECT 1 FROM web_enrolments WHERE parent_enrolment_id=$1", enrolment_id):
        raise ConflictError("Year 2 has already been started")
    n = int(instalments)
    if not 1 <= n <= 36:
        raise ValidationError("Use 1 to 36 instalments")
    per = core.money(amount, True)
    first = core.day(first_due)
    y2 = await one(c, """INSERT INTO enrolments(learner_id,programme_code,start_date,total_fee,instalments,counsellor)
        VALUES($1,$2,$3,$4,$5,'New website · Year 2') RETURNING *""", e["learner_id"], e["programme_code"], first, per * n, n)
    for i in range(1, n + 1):
        await c.execute("INSERT INTO instalments(enrolment_id,seq,due_date,amount) VALUES($1,$2,$3,$4)", y2["id"], i, core.add_months(first, i - 1), per)
    await c.execute("""INSERT INTO web_enrolments(enrolment_id,web_order_id,course_id,plan_code,plan_label,year,parent_enrolment_id,activated_at,auto,
            instalment_count,instalment_amount) VALUES($1,$2,$3,'monthly','Year 2 · monthly',2,$4,now(),true,$5,$6)""",
                    y2["id"], we["web_order_id"], we["course_id"], enrolment_id, n, per)
    await audit(c, actor, "start_year2", "enrolment", y2["id"], dict(parent=str(enrolment_id), instalments=n, amount=str(per), first_due=first))
    return dict(ok=True, enrolment_id=y2["id"], instalments=n, total=per * n)


async def set_auto(c, enrolment_id, auto: bool, actor: str) -> dict:
    r = await c.execute("UPDATE web_enrolments SET auto=$2 WHERE enrolment_id=$1", enrolment_id, bool(auto))
    if r.endswith(" 0"):
        raise NotFoundError("Website enrolment not found")
    await audit(c, actor, "set_auto", "enrolment", enrolment_id, dict(auto=bool(auto)))
    return dict(ok=True)


async def cancel_order(c, order_id, actor: str) -> dict:
    o = await one(c, "SELECT * FROM web_orders WHERE id=$1 FOR UPDATE", order_id)
    if not o:
        raise NotFoundError("Order not found")
    if o["learner_id"]:
        raise ConflictError("Money was received for this order; withdraw the enrolment in Learners & fees instead")
    await c.execute("UPDATE web_orders SET status='cancelled', updated_at=now() WHERE id=$1", order_id)
    await c.execute("UPDATE bank_slips SET status='rejected', reason='Order cancelled', reviewed_by=$2, reviewed_at=now() WHERE web_order_id=$1 AND status='pending'",
                    order_id, actor)
    await audit(c, actor, "cancel", "web_order", order_id)
    return dict(ok=True)


async def expire_old_orders(c, days: int = 30) -> int:
    r = await c.execute(f"""UPDATE web_orders o SET status='expired', updated_at=now() WHERE status='pending' AND created_at < now() - interval '{int(days)} days'
        AND NOT EXISTS (SELECT 1 FROM bank_slips s WHERE s.web_order_id=o.id AND s.status='pending')""")
    return int(r.split()[-1])
