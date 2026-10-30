"""Page payloads for the Admissions Studio."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.insights import demo_data
from app.modules.insights.forecast import mission_forecast
from app.modules.insights.snapshot import load_snapshot

TIERS = [("Rookie", 0), ("Advisor", 1500), ("Mentor", 3000), ("Pathfinder", 5000), ("Legend", 8000)]


def _tier(xp: int) -> dict:
    index = 0
    for position, (_, floor) in enumerate(TIERS):
        if xp >= floor:
            index = position
    name, floor = TIERS[index]
    nxt = TIERS[index + 1] if index + 1 < len(TIERS) else None
    progress = 1 if nxt is None else (xp - floor) / (nxt[1] - floor)
    return {"name": name, "index": index, "floor": floor, "next": nxt[0] if nxt else None, "next_xp": nxt[1] if nxt else None, "progress": round(progress, 3)}


def _xp(person: dict) -> int:
    return person["assigned"] * 5 + person["contacted"] * 40 + person["enrolled"] * 500 + person["streak"] * 15


def _cumulative(daily: list[dict], days: int) -> dict:
    window = daily[-days:] if days else daily
    running = []
    total = 0
    for point in window:
        total += point["count"]
        running.append(total)
    labels = []
    if window:
        labels = [[0, window[0]["date"][5:]], [len(window) - 1, window[-1]["date"][5:]]]
        if len(window) > 4:
            mid = len(window) // 2
            labels.insert(1, [mid, window[mid]["date"][5:]])
    return {"series": running, "labels": labels, "source": "cms"}


def _signals(snap: dict) -> list[dict]:
    old = next(item["count"] for item in snap["aging"] if item["key"] == "7d+")
    referral = snap["referral_rate"]
    meta = snap["meta_rate"]
    items = [
        {"tone": "bad", "title": f"{old} new leads are 7+ days old", "detail": "Every day of delay cuts the chance of a first conversation.", "source": "cms"},
        {"tone": "good", "title": f"Referrals convert at {referral}% vs Meta at {meta}%", "detail": "A student-referral reward is the cheapest growth lever.", "source": "cms"},
        {"tone": "brand", "title": f"Best call window: {snap['peak']}", "detail": "Shift uncontactable retries into the hours people actually answer.", "source": snap["heatmap_source"]},
        {"tone": "warn", "title": f"{snap['cant_afford']} Can't Afford leads could be rescued", "detail": "Offer a monthly instalment plan.", "source": "cms"},
    ]
    return items


async def dashboard(db: AsyncSession, counsellor_id: int | None) -> dict:
    snap = await load_snapshot(db, counsellor_id)
    videos = snap["published_by_type"].get("video", 0)
    pdfs = snap["published_by_type"].get("pdf", 0)
    other = max(0, snap["published"] - videos - pdfs)
    return {
        "source": "cms",
        "generated_at": snap["generated_at"],
        "kpis": [
            {"label": "Total programmes", "value": snap["programs"], "sub": "in the catalogue", "source": "cms"},
            {"label": "Total students", "value": snap["students"], "sub": "registered LMS students", "source": "cms"},
            {"label": "CRM leads", "value": snap["total"], "sub": f"{snap['awaiting']} awaiting contact", "source": "cms"},
            {"label": "Outstanding", "value": "LKR 4.2M", "sub": "demo · fees not connected", "source": "demo"},
            {"label": "Published content", "value": snap["published"], "sub": f"{videos} videos · {pdfs} PDFs · {other} other", "source": "cms"},
        ],
        "secondary": [
            {"label": "Programme enquiries", "value": snap["total"], "sub": f"{snap['active_month']} active this month"},
            {"label": "Awaiting counselling", "value": snap["in_progress"], "sub": "open counselling stages"},
            {"label": "Conversion", "value": f"{snap['conversion']}%", "sub": f"{snap['enrolled']} confirmed enrolments"},
        ],
        "pending": [
            {"label": "Leads awaiting first contact", "count": snap["awaiting"], "tone": "warn", "goto": "crm-pipeline", "source": "cms"},
            {"label": "Inquiries in counselling pipeline", "count": snap["in_progress"], "tone": "brand", "goto": "crm-pipeline", "source": "cms"},
            {"label": "Invoices overdue more than 7 days", "count": 18, "tone": "bad", "goto": "payment-overview", "source": "demo"},
            {"label": "Learning content items in draft", "count": snap["draft"], "tone": "", "goto": "bots-overview", "source": "cms"},
        ],
        "activity": snap["activity"][:6],
        "top_interest": snap["top_interest"],
        "funnel": snap["stages"],
    }


async def command_centre(db: AsyncSession, counsellor_id: int | None, days: int) -> dict:
    snap = await load_snapshot(db, counsellor_id)
    today = date.today()
    end = date.fromisoformat(settings.MISSION_END_DATE)
    start = date.fromisoformat(settings.MISSION_START_DATE)
    monthly = snap["month_enrolled"]
    forecast = mission_forecast(current=snap["students"], target=settings.MISSION_TARGET_ENROLMENTS, today=today, end=end, recent_monthly=monthly)
    gap = (
        "On track."
        if forecast["on_track"]
        else f"That's {forecast['short']} short. The gap sits in Uncontactable recovery."
    )
    sparks = [point["count"] for point in snap["daily"][-14:]]
    return {
        "generated_at": snap["generated_at"],
        "mission": {
            "target": settings.MISSION_TARGET_ENROLMENTS,
            "current": snap["students"],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days_left": forecast["days_left"],
            "need_per_month": forecast["need_per_month"],
            "conversion": snap["conversion"],
            "landing": forecast["landing"],
            "on_track": forecast["on_track"],
            "gap": gap,
            "uncontactable": snap["uncontactable"],
            "source_students": "cms",
            "source_forecast": "modelled",
            "milestones": [
                {"name": "Phase 1 · Dec 2026", "target": 350, "current": snap["students"]},
                {"name": "Phase 2 · Mar 2027", "target": 650, "current": snap["students"]},
                {"name": "Final · Jun 2027", "target": settings.MISSION_TARGET_ENROLMENTS, "current": snap["students"]},
            ],
        },
        "kpis": [
            {"label": "CRM leads", "value": snap["total"], "detail": f"{snap['awaiting']} awaiting first contact", "tone": "brand", "spark": sparks, "source": "cms"},
            {"label": "Active this month", "value": snap["active_month"], "detail": "new enquiries in 30 days", "tone": "con", "spark": [point["count"] for point in snap["daily"][-30:]], "source": "cms"},
            {"label": "Counselling queue", "value": snap["in_progress"], "detail": "open stages before a decision", "tone": "ca", "spark": sparks, "source": "cms"},
            {"label": "Enrolments", "value": snap["enrolled"], "detail": f"{snap['conversion']}% of all leads", "tone": "enr", "spark": sparks, "source": "cms"},
        ],
        "stages": snap["stages"],
        "total": snap["total"],
        "biggest": {"label": "Contacted", "count": snap["contacted"]},
        "cumulative": _cumulative(snap["daily"], days),
        "forecast": forecast,
        "sources": snap["sources"],
        "programmes": snap["programmes"],
        "signals": _signals(snap),
        "heatmap": {"rows": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "start_hour": 8, "values": snap["heatmap"], "source": snap["heatmap_source"]},
        "peak": snap["peak"],
        "aging": snap["aging"],
        "past_sla": snap["past_sla"],
        "awaiting": snap["awaiting"],
    }


async def metrics(db: AsyncSession, counsellor_id: int | None) -> dict:
    snap = await load_snapshot(db, counsellor_id)
    today = date.today()
    end = date.fromisoformat(settings.MISSION_END_DATE)
    forecast = mission_forecast(current=snap["students"], target=settings.MISSION_TARGET_ENROLMENTS, today=today, end=end, recent_monthly=snap["month_enrolled"])
    sla = round((1 - snap["past_sla"] / snap["awaiting"]) * 100) if snap["awaiting"] else 100
    contact_rate = round((snap["total"] - snap["awaiting"]) / snap["total"] * 100) if snap["total"] else 0
    meta = demo_data.META
    cpl = round(meta["spend"] / meta["leads"])
    tree = [
        {"id": "root", "x": 400, "y": 30, "title": "Active students", "value": f"{snap['students']} / {settings.MISSION_TARGET_ENROLMENTS}", "tone": "bad", "source": "CMS"},
        {"id": "new", "x": 240, "y": 130, "title": "New students / month", "value": f"{snap['month_enrolled']} (need {forecast['need_per_month']})", "tone": "good" if snap["month_enrolled"] >= forecast["need_per_month"] else "warn", "source": "CMS", "parent": "root"},
        {"id": "ret", "x": 640, "y": 130, "title": "Retention", "value": "75% (dropout 25%)", "tone": "warn", "source": "Demo", "parent": "root"},
        {"id": "leads", "x": 100, "y": 240, "title": "Leads / month", "value": f"{snap['active_month']:,}", "tone": "good", "source": "CMS", "parent": "new"},
        {"id": "conv", "x": 400, "y": 240, "title": "Lead → enrol", "value": f"{snap['conversion']}%", "tone": "good" if snap["conversion"] >= 4 else "warn" if snap["conversion"] >= 2.5 else "bad", "source": "CMS", "parent": "new"},
        {"id": "att", "x": 560, "y": 240, "title": "Attendance", "value": "86% (demo)", "tone": "good", "source": "Demo", "parent": "ret"},
        {"id": "pay", "x": 720, "y": 240, "title": "Paying on time", "value": "82%", "tone": "warn", "source": "Demo", "parent": "ret"},
        {"id": "paid", "x": 10, "y": 350, "title": "Meta leads", "value": f"{meta['leads']:,} · CPL {cpl:,}", "tone": "bad", "source": "Demo", "parent": "leads"},
        {"id": "part", "x": 180, "y": 350, "title": "Partner & organic", "value": "≈ 40% of leads", "tone": "good", "source": "Demo", "parent": "leads"},
        {"id": "cont", "x": 340, "y": 350, "title": "Contact rate", "value": f"{contact_rate}%", "tone": "warn" if contact_rate < 60 else "good", "source": "CMS", "parent": "conv"},
        {"id": "sla", "x": 500, "y": 350, "title": "First call < 24h", "value": f"{sla}%", "tone": "warn" if sla < 80 else "good", "source": "Modelled", "parent": "conv"},
    ]
    tiles = [
        {"label": "Leads this month", "value": f"{snap['active_month']:,}", "delta": "live", "tone": "good", "source": "CMS", "spark": [point["count"] for point in snap["daily"][-8:]]},
        {"label": "Awaiting first contact", "value": str(snap["awaiting"]), "delta": f"{snap['past_sla']} past SLA", "tone": "bad", "source": "CMS", "spark": [item["count"] for item in snap["aging"]]},
        {"label": "Contact rate", "value": f"{contact_rate}%", "delta": "of all leads", "tone": "warn", "source": "CMS", "spark": [contact_rate] * 4},
        {"label": "Lead → enrol", "value": f"{snap['conversion']}%", "delta": "live", "tone": "good", "source": "CMS", "spark": [snap["conversion"]] * 4},
        {"label": "Meta spend · 30d", "value": f"LKR {meta['spend'] / 1e6:.1f}M", "delta": "demo snapshot", "tone": "warn", "source": "Demo", "spark": meta["cost"][::4]},
        {"label": "Meta cost per lead", "value": f"LKR {cpl:,}", "delta": "demo snapshot", "tone": "bad", "source": "Demo", "spark": [round(c / max(1, meta['daily_leads'][i])) for i, c in enumerate(meta["cost"]) if i % 4 == 0]},
        {"label": "Blended CAC", "value": "LKR 30,709", "delta": "−6%", "tone": "good", "source": "Demo", "spark": [36, 35, 34, 33, 32, 31, 31, 30.7]},
        {"label": "Return on spend", "value": "8.1×", "delta": "+0.4", "tone": "good", "source": "Demo", "spark": [7, 7.2, 7.4, 7.6, 7.8, 7.9, 8, 8.1]},
        {"label": "Visitor → lead", "value": "2.9%", "delta": "+0.2 pts", "tone": "good", "source": "Demo", "spark": [2.4, 2.5, 2.6, 2.6, 2.7, 2.8, 2.8, 2.9]},
        {"label": "Overseas visitors", "value": "18%", "delta": "+3 pts", "tone": "good", "source": "Demo", "spark": [12, 13, 14, 15, 15, 16, 17, 18]},
        {"label": "WhatsApp median reply", "value": f"{snap['median_reply_min']} min" if snap["median_reply_min"] else "13 min", "delta": "modelled" if snap["median_reply_min"] else "demo", "tone": "good", "source": "Modelled" if snap["median_reply_min"] else "Demo", "spark": [19, 18, 17, 16, 15, 14, 13]},
        {"label": "Partner enrolments", "value": "230", "delta": "+24", "tone": "good", "source": "Demo", "spark": [150, 162, 175, 188, 200, 210, 220, 230]},
        {"label": "Outstanding fees", "value": "LKR 4.2M", "delta": "+0.3M", "tone": "bad", "source": "Demo", "spark": [3.4, 3.5, 3.7, 3.8, 3.9, 4, 4.1, 4.2]},
        {"label": "Payments on time", "value": "82%", "delta": "−1 pt", "tone": "warn", "source": "Demo", "spark": [85, 85, 84, 84, 83, 83, 82, 82]},
        {"label": "Dropout ratio", "value": "25%", "delta": "stated", "tone": "warn", "source": "Demo", "spark": [27, 26, 25, 25]},
        {"label": "Active students", "value": str(snap["students"]), "delta": f"goal {settings.MISSION_TARGET_ENROLMENTS}", "tone": "bad", "source": "CMS", "spark": [snap["students"]] * 4},
    ]
    return {
        "tree": tree,
        "tiles": tiles,
        "cohorts": demo_data.retention_cohorts(),
        "cohorts_source": "demo",
        "response": snap["response_buckets"],
        "response_source": snap["response_source"],
        "velocity": snap["velocity"],
        "weekday": snap["weekday"],
        "weekday_source": "cms",
    }


def _recommendations(snap: dict) -> list[dict]:
    return [
        {"title": "Clear the first-contact backlog inside 24 hours", "why": f"{snap['awaiting']} leads await a first call.", "impact": round(snap["awaiting"] * 0.035, 1)},
        {"title": "Re-call Uncontactable leads in the evening window", "why": f"{snap['uncontactable']} leads. A small recovery adds students quickly.", "impact": round(snap["uncontactable"] * 0.03 * 0.12, 1)},
        {"title": "Launch the Champions referral programme", "why": "Referrals convert better than paid ads at a fraction of the cost.", "impact": 6},
        {"title": f"Offer instalments to {snap['cant_afford']} Can't Afford leads", "why": "A monthly plan removes the main objection for this group.", "impact": round(snap["cant_afford"] * 0.08, 1)},
        {"title": "Refresh the tired Meta campaigns", "why": "The demo snapshot shows cost per lead rising while volume falls.", "impact": 3.2},
    ]


async def brain(db: AsyncSession, counsellor_id: int | None) -> dict:
    snap = await load_snapshot(db, counsellor_id)
    meta = demo_data.META
    first = sum(meta["cost"][:7]) / max(1, sum(meta["daily_leads"][:7]))
    last = sum(meta["cost"][-7:]) / max(1, sum(meta["daily_leads"][-7:]))
    slow = [person for person in snap["counsellors"] if (person["median_reply_min"] or 0) > 30]
    return {
        "datasets": [
            {"key": "meta", "name": "Meta Ads", "source": "Demo", "blurb": "Spend, leads, cost per lead, age, gender and region for every campaign."},
            {"key": "channels", "name": "All channels", "source": "Demo", "blurb": "Spend, leads and enrolments across all 10 marketing channels."},
            {"key": "visitors", "name": "Website visitors", "source": "Demo", "blurb": "Website traffic, personas, countries, devices and buying intent."},
            {"key": "pipeline", "name": "Admissions pipeline", "source": "CMS", "blurb": "Every lead by stage, score and age, from the CRM."},
            {"key": "team", "name": "Counsellors", "source": "CMS", "blurb": "Counsellor workload, contact rates, conversions and reply speed."},
            {"key": "partners", "name": "Partner links", "source": "CMS", "blurb": "Clicks, leads and enrolments for every tracked partner link."},
            {"key": "finance", "name": "Invoices & payments", "source": "Demo", "blurb": "Outstanding invoices, overdue payments and instalment behaviour."},
            {"key": "content", "name": "Content & LMS", "source": "CMS", "blurb": "Programme catalogue, LMS students and published learning content."},
        ],
        "signals": [
            {"key": "meta", "tone": "bad", "title": "Cost per lead rising", "detail": f"LKR {last:,.0f} last week vs LKR {first:,.0f} four weeks ago (demo snapshot)."},
            {"key": "pipeline", "tone": "warn", "title": "First-contact backlog", "detail": f"{snap['awaiting']} leads are waiting for a first call."},
            {"key": "team", "tone": "warn", "title": "Reply speed gap", "detail": f"{len(slow)} counsellor{'s' if len(slow) != 1 else ''} reply slower than 30 minutes." if slow else "Reply times are inside the window we can measure."},
            {"key": "partners", "tone": "good", "title": "Partners outperform paid ads", "detail": "Tutor and agent links convert several times better per click in the sample set."},
            {"key": "visitors", "tone": "brand", "title": "Parents are a hidden segment", "detail": "18% of visitors are overseas and read fees and accreditation pages (demo)."},
            {"key": "finance", "tone": "warn", "title": "Overdue payments", "detail": "18 invoices are 7+ days overdue (demo)."},
        ],
        "recommendations": _recommendations(snap),
        "predictions": [
            {"label": "Active students by 30 Jun 2027", "value": str(mission_forecast(current=snap["students"], target=settings.MISSION_TARGET_ENROLMENTS, today=date.today(), end=date.fromisoformat(settings.MISSION_END_DATE), recent_monthly=snap["month_enrolled"])["landing"]), "note": f"goal {settings.MISSION_TARGET_ENROLMENTS}", "pct": min(1, snap["students"] / settings.MISSION_TARGET_ENROLMENTS), "source": "modelled"},
            {"label": "Enrolments next 30 days", "value": str(round(snap["expected_enrolments"])), "note": "from open pipeline", "pct": 0.5, "source": "modelled"},
            {"label": "Dropout risk this term", "value": "25%", "note": "stated, not measured yet", "pct": 0.25, "source": "demo"},
            {"label": "Payments collected on time", "value": "82%", "note": "demo until PayHere is connected", "pct": 0.82, "source": "demo"},
        ],
    }


async def context_text(db: AsyncSession, counsellor_id: int | None) -> str:
    snap = await load_snapshot(db, counsellor_id)
    stages = ", ".join(f"{item['label']} {item['count']}" for item in snap["stages"])
    team = "\n".join(
        f"{person['name']}: {person['assigned']} assigned, {person['contacted']} contacted, {person['enrolled']} enrolled, reply {person['median_reply_min'] or 'n/a'} min, streak {person['streak']}"
        for person in snap["counsellors"]
    ) or "No counsellors in this view."
    return (
        f"ORGANISATION: Inspire College, Colombo. Mission: {settings.MISSION_TARGET_ENROLMENTS} active students "
        f"by {settings.MISSION_END_DATE} (from {settings.MISSION_START_DATE}). Currently {snap['students']} LMS students.\n"
        f"CRM: {snap['total']} leads, {snap['awaiting']} awaiting first contact, {snap['in_progress']} in progress, "
        f"{snap['enrolled']} enrolled ({snap['conversion']}% conversion), {snap['active_month']} new this month. "
        f"Leading interest {snap['top_interest']['name']} ({snap['top_interest']['count']}).\n"
        f"STAGES: {stages}.\n"
        f"MODELLED: referrals {snap['referral_rate']}% vs meta {snap['meta_rate']}%; best call window {snap['peak']}; "
        f"{snap['past_sla']} new leads past 24h SLA; open-pipeline expected enrolments {snap['expected_enrolments']}.\n"
        f"{demo_data.meta_summary()}\n"
        f"TEAM (last 30 days):\n{team}"
    )


async def dataset_summary(db: AsyncSession, key: str, counsellor_id: int | None) -> tuple[str, str]:
    snap = await load_snapshot(db, counsellor_id)
    if key == "meta":
        return "Meta Ads", demo_data.meta_summary()
    if key == "channels":
        rows = "; ".join(f"{item['name']} LKR {item['spend']:.0f}/{item['leads']}/{item['enrolled']}" for item in demo_data.CHANNELS)
        return "All channels", "Channels (spend / leads / enrolled, demo except where a live connector exists): " + rows
    if key == "visitors":
        visitors = demo_data.VISITORS
        return "Website visitors", f"Demo: {visitors['users_7d']} users, {visitors['sessions_7d']} sessions, {visitors['visitor_to_lead']}% visitor to lead, {visitors['overseas']}% overseas, {visitors['mobile']}% mobile."
    if key == "pipeline":
        return "Admissions pipeline", await context_text(db, counsellor_id)
    if key == "team":
        return "Counsellors", await context_text(db, counsellor_id)
    if key == "finance":
        return "Invoices & payments", "Demo: outstanding LKR 4.2M, 18 invoices overdue 7+ days. Can't Afford leads: " + str(snap["cant_afford"]) + "."
    if key == "content":
        return "Content & LMS", f"CMS: {snap['programs']} programmes, {snap['students']} LMS students, {snap['published']} published items, {snap['draft']} drafts."
    if key == "partners":
        return "Partner links", "Use the partner link table. Real clicks and CRM leads matched by referral code. Sample channel stats are not invented."
    return "Admissions", await context_text(db, counsellor_id)


async def studio(db: AsyncSession, tab: str, counsellor_id: int | None) -> dict:
    snap = await load_snapshot(db, counsellor_id)
    meta = demo_data.META
    clicks = sum(meta["clicks"])
    cpl = meta["spend"] / meta["leads"]
    first = sum(meta["cost"][:7]) / max(1, sum(meta["daily_leads"][:7]))
    last = sum(meta["cost"][-7:]) / max(1, sum(meta["daily_leads"][-7:]))
    if tab == "overview":
        return {"tab": tab, "meta": meta, "clicks": clicks, "cpl": round(cpl), "cpl_first": round(first), "cpl_last": round(last), "pages": demo_data.PAGES, "pipeline_sources": snap["sources"], "stages": snap["stages"]}
    if tab == "channels":
        return {"tab": tab, "channels": demo_data.CHANNELS, "source": "demo", "crm_sources": snap["sources"]}
    if tab == "visitors":
        return {"tab": tab, "visitors": demo_data.VISITORS, "pages": demo_data.PAGES}
    if tab == "tracker":
        return {"tab": tab, "stages": snap["stages"], "total": snap["total"], "hot": snap["hot"], "warm": snap["warm"], "open": snap["open_leads"], "source": "cms"}
    if tab == "champions":
        return {"tab": tab, "champions": demo_data.CHAMPIONS, "source": "demo", "ladder": [{"name": name, "xp": floor} for name, floor in TIERS]}
    if tab == "lab":
        return {"tab": tab, "recommendations": _recommendations(snap), "meta_campaigns": meta["campaigns"], "source": "demo"}
    if tab == "connect":
        return {"tab": tab, "platforms": demo_data.PLATFORMS, "events": demo_data.GTM_EVENTS}
    return {"tab": "overview"}
