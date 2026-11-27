"""Server-side fee plans for the new website. The browser never decides an amount.

HND and Foundation use the shared plan table (Website settings -> fee plans); short courses and postgraduate courses
are priced from the course fee. Each plan carries display text (t, d, a, u, then) plus the billing terms:
code (monthly | full | reserve), today (due at enrolment), instalments and instalment_amount (monthly only).
"""
from decimal import Decimal

DEFAULT_FEE_PLANS = {
    "hnd": [
        {"t": "Monthly · start with LKR 9,999", "d": "LKR 10,000 registration today, then 9 × LKR 9,999 in Year 1", "a": "LKR 9,999", "u": "a month",
         "today": 10000, "then": "9 × LKR 9,999 (Year 1)", "hot": True, "code": "monthly", "instalments": 9, "instalment_amount": 9999},
        {"t": "Pay Year 1 in full", "d": "One payment for Year 1. Year 2 billed later.", "a": "LKR 99,900", "u": "Year 1", "today": 99900,
         "then": "Year 2 billed later", "code": "full"},
        {"t": "Just reserve my seat", "d": "Pay registration now, choose your plan with an advisor", "a": "LKR 10,000", "u": "registration",
         "today": 10000, "then": "Plan set with an advisor", "code": "reserve"},
    ],
    "fnd": [
        {"t": "Monthly · LKR 5,000", "d": "LKR 9,500 registration today, then 8 × LKR 5,000", "a": "LKR 5,000", "u": "a month", "today": 9500,
         "then": "8 × LKR 5,000", "hot": True, "code": "monthly", "instalments": 8, "instalment_amount": 5000},
        {"t": "Pay in full", "d": "One payment, done", "a": "LKR 49,500", "u": "total", "today": 49500, "then": "Nothing more", "code": "full"},
        {"t": "Just reserve my seat", "d": "Pay registration now, start when ready", "a": "LKR 9,500", "u": "registration", "today": 9500,
         "then": "Plan set with an advisor", "code": "reserve"},
    ],
}
# Prefill for "Start Year 2" (finance can change both numbers when starting it).
DEFAULT_YEAR2 = {"hnd": {"instalments": 9, "instalment_amount": 9999}}
# Accounts programme used when a course's website listing names none.
DEFAULT_PROGRAMME = {"hnd": "HND-COM", "fnd": "FOUND", "pg": "PGD", "cpd": "CPD"}


def lkr(n) -> str:
    return f"LKR {round(float(n or 0)):,}"


def fee_table(stored: dict | None) -> dict:
    out = {k: v for k, v in DEFAULT_FEE_PLANS.items()}
    for k, v in (stored or {}).items():
        if k in ("hnd", "fnd") and isinstance(v, list) and v:
            out[k] = v
    return out


def plans_for(course: dict, fee_plans: dict | None = None) -> list[dict]:
    table = fee_table(fee_plans)
    plan_type = course.get("plan_type")
    if plan_type in ("hnd", "fnd"):
        return table[plan_type]
    fee = int(course.get("fee") or 0)
    if plan_type == "cpd":
        return [
            {"t": "Pay in full", "d": "Your seat in the next cohort", "a": lkr(fee), "u": "total", "today": fee, "then": "Nothing more", "hot": True, "code": "full"},
            {"t": "Reserve my seat", "d": "Hold a seat, pay before the cohort starts", "a": "LKR 0", "u": "today", "today": 0, "then": "Pay before the cohort starts", "code": "reserve"},
        ]
    return [
        {"t": "Reserve my seat", "d": "No payment today. An advisor sets your monthly plan.", "a": "LKR 0", "u": "today", "today": 0,
         "then": "Monthly plan set with an advisor", "hot": True, "code": "reserve"},
        {"t": "Pay in full", "d": "One payment", "a": lkr(fee), "u": "total", "today": fee, "then": "Nothing more", "code": "full"},
    ]


def billing_terms(plan: dict, plan_type: str = "") -> dict:
    """code / today / instalments / instalment_amount for a plan, filling gaps for plans edited without them."""
    code = plan.get("code")
    if code not in ("monthly", "full", "reserve"):
        u = str(plan.get("u", "")).lower()
        code = "monthly" if "month" in u else "reserve" if ("registration" in u or "today" in u) else "full"
    inst = int(plan.get("instalments") or 0)
    amt = int(plan.get("instalment_amount") or 0)
    if code == "monthly" and (not inst or not amt):
        for d in DEFAULT_FEE_PLANS.get(plan_type, []):
            if d.get("code") == "monthly":
                inst, amt = inst or d["instalments"], amt or d["instalment_amount"]
    return {"code": code, "today": int(plan.get("today") or 0), "instalments": inst if code == "monthly" else 0,
            "instalment_amount": amt if code == "monthly" else 0, "label": str(plan.get("t", ""))[:200]}


def addon_price(course: dict) -> int:
    """Extra short courses chosen in the enrol drawer are 25% off."""
    return round(float(course.get("cpd_addon_fee") or course.get("fee") or 0) * 0.75)


def validate_fee_plans(value) -> dict:
    """Checks fee plans sent from the CMS before they are stored."""
    from app.core.errors import ValidationError
    if not isinstance(value, dict):
        raise ValidationError("Fee plans must be an object with hnd and fnd lists")
    out = {}
    for k in ("hnd", "fnd"):
        plans = value.get(k)
        if plans is None:
            continue
        if not isinstance(plans, list) or not 1 <= len(plans) <= 6:
            raise ValidationError(f"Give {k.upper()} between one and six plans")
        cleaned = []
        for p in plans:
            if not isinstance(p, dict) or not str(p.get("t", "")).strip():
                raise ValidationError("Every plan needs a title")
            terms = billing_terms(p, k)
            if terms["today"] < 0 or terms["today"] > 10_000_000:
                raise ValidationError("Enter a valid amount due today")
            if terms["code"] == "monthly" and (not 1 <= terms["instalments"] <= 36 or terms["instalment_amount"] <= 0):
                raise ValidationError("Monthly plans need 1-36 instalments and an instalment amount")
            if terms["code"] != "reserve" and terms["today"] <= 0:
                raise ValidationError("Monthly and full plans need an amount due today")
            cleaned.append({**{x: p.get(x) for x in ("t", "d", "a", "u", "then") if p.get(x) is not None}, "hot": bool(p.get("hot")),
                            "code": terms["code"], "today": terms["today"],
                            **({"instalments": terms["instalments"], "instalment_amount": terms["instalment_amount"]} if terms["code"] == "monthly" else {})})
        out[k] = cleaned
    return out


def money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))
