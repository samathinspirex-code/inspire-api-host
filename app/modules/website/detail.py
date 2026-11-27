"""Turn a catalogue course (academic_courses) plus its website listing (website_courses) into the course object the
new website's design pages use. Pure functions: no database access.

Live catalogue fields (title, banner, price, duration, entry requirements, topics, outcomes) always come from the CMS
course, so edits in Academic Structure show on the website. The website listing adds the design-only parts (design key,
card text, fee plan type, featured flag) and, for the 30 courses drawn in the design, their hand-made programme page.
"""
import re

# The 30 courses drawn in the design: design key -> catalogue slug. Pages hard-code some of these keys.
KEY_TO_SLUG = {
    "hnd-se": "hnd-in-computing-software-engineering-software-developer-pathway",
    "hnd-ai": "hnd-in-artificial-intelligence",
    "hnd-cyber": "hnd-in-computing-cyber-security-technologist-pathway",
    "hnd-data": "hnd-in-computing-data-analytics-data-technologist-pathway",
    "hnd-net": "hnd-in-computing-netdevops-engineer-pathway",
    "hnd-acc": "hnd-in-accounting",
    "f-idt": "foundation-in-information-digital-technologies",
    "f-bus": "foundation-in-business-management",
    "f-acc": "foundation-in-accounting",
    "f-stat": "foundation-in-applied-statistics",
    "f-eng": "foundation-in-english-communications-for-business",
    "f-sme": "foundation-in-small-business-social-enterprise-start-up",
    "pg-edu": "postgraduate-diploma-in-strategic-educational-leadership-and-management",
    "pg-ai": "postgraduate-diploma-in-computing-artificial-intelligence",
    "pg-gen": "postgraduate-diploma-in-computing-general-route",
    "pg-se": "postgraduate-diploma-in-computing-software-engineering",
    "pg-app": "postgraduate-diploma-in-computing-application-development",
    "pg-data": "postgraduate-diploma-in-computing-data-analytics",
    "tu-beng": "uob-beng-hons-software-engineering-top-up",
    "tu-fin": "top-up-degree-in-management-finance-pathway",
    "tu-gen": "top-up-degree-in-management-general-route",
    "tu-people": "top-up-degree-in-management-managing-people-pathway",
    "tu-ops": "top-up-degree-in-management-operations-management-pathway",
    "tu-sales": "top-up-degree-in-management-sales-marketing-pathway",
    "c-aimastery": "ai-mastery",
    "c-aihr": "ai-for-hr-cpd",
    "c-aimkt": "ai-for-marketing",
    "c-cyber": "cyber-security-in-the-ai-era-short-course-cpd",
    "c-looker": "data-analytics-with-google-looker-studio-cpd",
    "c-dm": "digital-marketing-analytics-essentials",
}
# Keys the page code reads directly; the CMS readiness check warns when one of them is missing or hidden.
REQUIRED_KEYS = ["hnd-se", "hnd-ai", "hnd-cyber", "hnd-data", "f-bus", "c-aimastery", "c-cyber", "c-looker", "c-dm", "c-aimkt", "c-aihr"]

LEVEL = {"HND": "HND", "Foundation": "Foundation", "Postgraduate": "Postgraduate", "MBA": "Postgraduate", "MCA": "Postgraduate",
         "Top-Up": "Top-Up", "BCA": "Top-Up", "BBA": "Top-Up", "B.COM": "Top-Up", "Short Courses": "Short course",
         "Advanced Diploma": "HND", "Higher Diploma": "HND", "Diploma": "Foundation"}
PLAN = {"HND": "hnd", "Foundation": "fnd", "Short course": "cpd"}
LEVELS = ["HND", "Foundation", "Top-Up", "Postgraduate", "Short course"]

PRESETS = {
    "HND": dict(whoTitle="For builders and problem-solvers.", who=["School leavers after O/Ls or A/Ls", "Career changers", "Working learners", "Future degree students"],
                cups="48 free coffees a year", feeTitle="Plan your diploma with an advisor.", path_next="UK top-up degree"),
    "Foundation": dict(whoTitle="For your first step after O/Ls.", who=["Students who have finished O/Ls", "Learners who want a head start", "Anyone planning an HND or degree", "Career starters"],
                       cups="32 free coffees over 8 months", feeTitle="UK qualifications with monthly plans.", path_next="Level 4 diploma or HND"),
    "Top-Up": dict(whoTitle="For graduates ready to finish a degree.", who=["HND graduates", "Working professionals with a Level 5", "Learners after a recognised diploma", "Future managers"],
                   cups="40 free coffees", feeTitle="Finish your degree online.", path_next="Master's"),
    "Postgraduate": dict(whoTitle="For professionals ready to go further.", who=["Graduates", "Experienced professionals", "Managers and team leads", "Career switchers"],
                         cups="40 free coffees", feeTitle="Invest in your next level.", path_next="Senior and specialist roles"),
    "Short course": dict(whoTitle="For professionals who want skills fast.", who=["Working professionals", "Managers and team leads", "Entrepreneurs", "Students adding a skill"],
                         cups="4 free coffees in your first month", feeTitle="One clear fee.", path_next="Apply it at work"),
}
# Fields every programme page reads. A course is never sent without them, so the page cannot break.
REQUIRED_DETAIL = ("lvl", "plan", "short", "name", "qual", "body", "img", "alt", "chip", "blurb", "overview", "years", "skills", "careers",
                   "dur", "fee", "feeHead", "monthTrust", "startAmt", "startNote", "fees", "feeTitle", "feeNote", "who", "whoTitle", "cups",
                   "entry", "path", "finalLine")
# Values refreshed from the live catalogue even on hand-made design pages.
LIVE_KEYS = ("name", "qual", "img", "alt", "blurb", "overview", "skills", "careers", "entry", "progression")


def lkr(n) -> str:
    return f"LKR {round(float(n or 0)):,}"


def clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("\t", " ")).strip()


def paragraphs(s) -> list[str]:
    return [clean(p) for p in str(s or "").split("\n") if clean(p)]


def study(c: dict) -> tuple[int, str]:
    opts = [o for o in (c.get("study_options") or []) if o.get("is_enabled")]
    prices = [int(float(o["price"])) for o in opts if o.get("price")]
    dur = next((o.get("duration") for o in opts if o.get("duration")), "") or ""
    return (min(prices) if prices else 0), clean(dur)


def real_detail(c: dict) -> dict:
    out = {}
    entry = paragraphs(c.get("entry_requirements"))
    if entry:
        out["entry"] = " · ".join(entry)
    if c.get("progression_route"):
        out["progression"] = clean(c["progression_route"])
    return out


def plan_fees(c: dict, fee_head: str, dur: str) -> list:
    cards = ((c.get("payment_plans") or {}).get("cards") or []) if c.get("payment_plans_enabled") else []
    out = []
    for card in cards:
        rows = [[clean(r.get("label")), clean(r.get("amount"))] for r in card.get("rows") or [] if r.get("label")]
        if rows:
            out.append({"h": clean(card.get("title") or card.get("heading") or "Programme fee"), "rows": rows})
    return out or [{"h": "Programme fee", "rows": [["Fee", fee_head], ["Duration", dur or "Ask an advisor"], ["Payment plan", "Set with an advisor"]]}]


def new_course_detail(c: dict, level: str, plan: str, short: str, fee: int, dur: str) -> dict:
    """A complete programme page object built from the catalogue course alone."""
    paras = paragraphs(c.get("blurb"))
    topics = [clean(t.get("topic")) for t in sorted(c.get("topics") or [], key=lambda t: t.get("order") or 0)]
    units = [t for t in topics if t and not re.match(r"^(year|level)\s*\d", t, re.I)]
    outcomes = [clean(o.get("outcome")) for o in sorted(c.get("outcomes") or [], key=lambda o: o.get("order") or 0)]
    outcomes = [o for o in outcomes if o]
    pre = PRESETS.get(level, PRESETS["Short course"])
    monthly = int(float(c.get("monthly_instalment") or 0))
    fee_head = ("from " + lkr(fee) if level != "Short course" else lkr(fee)) if fee else "Talk to an advisor"
    d = {
        "lvl": level, "plan": plan, "short": short, "name": clean(c.get("title")),
        "qual": clean((c.get("subtitle") or "").split("|")[-1]) or level, "body": clean(c.get("awarding_body")) or "UK-regulated",
        "img": c.get("image_url") or "", "alt": clean(c.get("title")), "chip": clean(c.get("awarding_body")) or level,
        "blurb": paras[0] if paras else "", "overview": " ".join(paras[1:3]) if len(paras) > 1 else (paras[0] if paras else ""),
        "years": [["Focus", units[0] if units else short], ["Format", "Live online sessions plus the Inspire Brain LMS."]],
        "skills": units[:6] or [short], "careers": outcomes[:5] or ["Talk to an advisor about career routes"],
        "dur": dur or "Ask an advisor", "fee": fee, "feeHead": fee_head,
        "offer": bool(c.get("limited_offer_enabled") and monthly),
        "monthTrust": f"From LKR {monthly:,} a month" if monthly else "Monthly plans available",
        "startAmt": f"LKR {monthly:,} / month" if monthly else fee_head,
        "startNote": "after registration" if monthly else "Payment plan set with an advisor",
        "fees": plan_fees(c, fee_head, dur), "feeTitle": pre["feeTitle"],
        "feeNote": "Payment plans are set with an advisor. Contact admissions for entry requirements.",
        "who": pre["who"], "whoTitle": pre["whoTitle"], "cups": pre["cups"],
        "entry": "Contact admissions for entry requirements.",
        "path": [["Today", "Where you are now"], ["You are here", short], ["Next", pre["path_next"]], ["Then", "Your next career step"]],
        "finalLine": "Talk to an advisor about your plan." if not fee else "Reserve your seat today.",
    }
    return {**d, **real_detail(c)}


def _num(v) -> int:
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def compose(c: dict, web: dict | None, index: int = 0) -> dict:
    """One bundle course. `c` is a row of academic.service.list_courses; `web` a website_courses row (or None)."""
    web = web or {}
    slug = c["slug"]
    key = web.get("design_key") or slug
    fee, dur = study(c)
    if _num(web.get("fee_override")):
        fee = _num(web.get("fee_override"))
    school = clean(web.get("school") or (c.get("school_name") or "").replace("School of ", ""))
    level = web.get("level") if web.get("level") in LEVELS else LEVEL.get(c.get("programme_name") or "", "Short course")
    plan = web.get("plan_type") or (PLAN.get(level, "pg") if level != "Foundation" else "fnd")
    title = clean(c.get("title"))
    short = clean(web.get("short_name")) or title.replace("-Level Study in", "").replace("Postgraduate-Level Study in", "PG ·")[:80]
    generated = new_course_detail(c, level, plan, short, fee, dur)
    stored = web.get("detail") if isinstance(web.get("detail"), dict) else None
    if stored and key in KEY_TO_SLUG:
        detail = {**generated, **stored}
        for k in LIVE_KEYS:
            if generated.get(k):
                detail[k] = generated[k]
        if dur:
            detail["dur"] = dur
        if fee:
            detail["fee"], detail["feeHead"] = fee, generated["feeHead"]
    else:
        detail = {**generated, **(stored or {})}
    if clean(web.get("short_name")):
        detail["short"] = short  # the listing's short name wins over the stored page text
    for k in REQUIRED_DETAIL:
        if detail.get(k) in (None, "", []):
            detail[k] = generated.get(k) if generated.get(k) not in (None, "", []) else detail.get(k, "")
    monthly = _num(c.get("monthly_instalment"))
    fee_label = (lkr(fee) if plan == "cpd" else "from " + lkr(fee)) if fee else (clean(web.get("fee_label")) or "Talk to an advisor")
    paras = paragraphs(c.get("blurb"))
    return {
        "course_id": c["course_id"], "key": key, "slug": slug, "title": title or key, "short": short, "level": level,
        "school": school, "body": clean(web.get("body_label")) or clean(c.get("awarding_body")) or school, "plan_type": plan,
        "duration": dur or clean(detail.get("dur")), "fee": fee, "fee_label": fee_label,
        "monthly_label": clean(web.get("monthly_label")) or (f"or LKR {monthly:,} a month" if monthly else ""),
        "cpd_addon_fee": _num(web.get("cpd_addon_fee")) or None, "image": c.get("image_url") or detail.get("img") or "",
        "blurb": generated.get("blurb") or detail.get("blurb") or "", "keywords": clean(web.get("keywords")) or " ".join(sorted({level.lower(), school.lower()})),
        "card_type": clean(web.get("card_type")) or level, "featured": bool(web.get("featured")),
        "sort_order": int(web.get("sort_order")) if web.get("sort_order") is not None else 100 + index,
        "auto_instalments": bool(web.get("auto_instalments", True)), "status": "published",
        "detail": detail, "seo_title": clean(web.get("seo_title")) or title,
        "seo_description": clean(web.get("seo_description")) or ((paras or [""])[0][:300]),
    }
