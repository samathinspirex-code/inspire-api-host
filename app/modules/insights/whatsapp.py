"""WhatsApp bot figures, measured from what the bot pushes into the CRM.

Only handovers reach the CRM, so nothing here can describe students who dropped
out earlier in the bot. A reply is either a CRM contact activity or a counsellor
message mirrored from the bot dashboard; replies sent from a counsellor's own
phone are invisible until the counsellor logs them.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.crm.service import CONTACT_ACTIVITY_TYPES
from app.modules.crm.whatsapp_bot_service import BOT_ACTIVITY_TYPE, WABOT_PREFIX

CONTACT_SQL = ", ".join(f"'{item}'" for item in sorted(CONTACT_ACTIVITY_TYPES))
RECENT_DAYS = 7
HANDOVER_PREFIX = "WhatsApp bot handover%"
MESSAGE_PREFIX = "WhatsApp message%"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(value: datetime | None) -> datetime | None:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value


def _is_new(item: dict) -> bool:
    return str(item["external_record_id"] or "").startswith(WABOT_PREFIX) and item["source"] == "whatsapp"


def summarize(rows: list[dict], messages: dict, now: datetime | None = None) -> dict:
    """Pure aggregation, kept apart from the SQL so the definitions are testable."""
    now = now or _now()
    leads = [{**row, "handed_at": _naive(row["handed_at"]), "first_reply_at": _naive(row["first_reply_at"])} for row in rows]
    leads.sort(key=lambda item: item["handed_at"], reverse=True)
    month = [item for item in leads if now - item["handed_at"] <= timedelta(days=30)]
    week = [item for item in leads if now - item["handed_at"] <= timedelta(days=RECENT_DAYS)]
    replies = [(item["first_reply_at"] - item["handed_at"]).total_seconds() / 60 for item in month if item["first_reply_at"]]
    waiting = [item for item in leads if not item["first_reply_at"] and item["stage"] == "new_lead"]
    enrolled = sum(1 for item in leads if item["stage"] == "enrolled")
    today = now.date()
    daily = Counter(item["handed_at"].date() for item in month)
    counsellors: dict[str, dict] = {}
    speeds: dict[str, list[float]] = {}
    for item in month:
        name = (item["counsellor_name"] if item["assigned_counsellor_id"] else None) or "Unassigned"
        person = counsellors.setdefault(name, {"name": name, "user_id": item["assigned_counsellor_id"], "leads": 0, "new_leads": 0, "replied": 0, "enrolled": 0})
        person["leads"] += 1
        person["new_leads"] += 1 if _is_new(item) else 0
        person["replied"] += 1 if item["first_reply_at"] else 0
        person["enrolled"] += 1 if item["stage"] == "enrolled" else 0
        if item["first_reply_at"]:
            speeds.setdefault(name, []).append((item["first_reply_at"] - item["handed_at"]).total_seconds() / 60)
    for name, person in counsellors.items():
        person["median_reply_min"] = round(median(speeds[name])) if name in speeds else None
    weekly = [0] * 8
    for item in leads:
        age = (now - item["handed_at"]).days // 7
        if 0 <= age < 8:
            weekly[7 - age] += 1
    return {
        "source": "cms",
        "total": len(leads),
        "month": len(month),
        "week": len(week),
        "new_leads": sum(1 for item in leads if _is_new(item)),
        "merged": sum(1 for item in leads if not _is_new(item)),
        "month_enrolled": sum(1 for item in month if item["stage"] == "enrolled"),
        "weekly": weekly,
        "enrolled": enrolled,
        "conversion": round(enrolled / len(leads) * 100, 1) if leads else 0,
        "unassigned": sum(1 for item in leads if not item["assigned_counsellor_id"]),
        "waiting": len(waiting),
        "waiting_over_day": sum(1 for item in waiting if now - item["handed_at"] > timedelta(hours=24)),
        "median_reply_min": round(median(replies)) if replies else None,
        "reply_sample": len(replies),
        "messages": messages,
        "last_handover_at": leads[0]["handed_at"].isoformat() if leads else None,
        "receiving": bool(week),
        "stages": [{"key": key, "count": count} for key, count in Counter(item["stage"] for item in leads).most_common()],
        "programmes": [{"name": name, "count": count} for name, count in Counter(item["programme"] for item in month).most_common(8)],
        "counsellors": sorted(counsellors.values(), key=lambda person: -person["leads"]),
        "daily": [{"date": str(day), "count": daily.get(day, 0)} for day in (today - timedelta(days=offset) for offset in range(29, -1, -1))],
        "recent": [
            {"lead_id": item["lead_id"], "name": item["full_name"], "programme": item["programme"], "stage": item["stage"],
             "counsellor": item["counsellor_name"] if item["assigned_counsellor_id"] else None,
             "handed_at": item["handed_at"].isoformat(), "replied": bool(item["first_reply_at"])}
            for item in leads[:12]
        ],
    }


async def load_whatsapp(db: AsyncSession, counsellor_id: int | None = None) -> dict:
    scope = " AND l.assigned_counsellor_id = :cid" if counsellor_id is not None else ""
    params = {"cid": counsellor_id} if counsellor_id is not None else {}
    bot = {"bot": BOT_ACTIVITY_TYPE, "handover": HANDOVER_PREFIX, "message": MESSAGE_PREFIX, "wabot": f"{WABOT_PREFIX}%"}
    rows = (await db.execute(text(f"""
        WITH handed AS (
          SELECT a.lead_id, min(a.created_at) AS first_at FROM crm_activities a
          WHERE a.activity_type = :bot AND a.content LIKE :handover GROUP BY a.lead_id
        )
        SELECT l.lead_id, l.full_name, l.stage, l.source, l.external_record_id, l.assigned_counsellor_id, l.counsellor_name,
               coalesce(nullif(l.interested_course, ''), nullif(l.interested_programme, ''), 'Unspecified') AS programme,
               coalesce(h.first_at, l.created_at) AS handed_at, r.first_reply_at
        FROM crm_leads l
        LEFT JOIN handed h ON h.lead_id = l.lead_id
        LEFT JOIN LATERAL (
          SELECT min(a.created_at) AS first_reply_at FROM crm_activities a
          WHERE a.lead_id = l.lead_id AND a.created_at >= coalesce(h.first_at, l.created_at)
            AND (a.activity_type IN ({CONTACT_SQL})
                 OR (a.activity_type = :bot AND a.content LIKE :message AND a.content LIKE '%' || chr(10) || 'Counsellor%'))
        ) r ON true
        WHERE l.is_archived = false{scope} AND (h.lead_id IS NOT NULL OR l.external_record_id LIKE :wabot)
    """), {**params, **bot})).mappings().all()
    counts = (await db.execute(text(f"""
        SELECT count(*) FILTER (WHERE a.content LIKE '%' || chr(10) || 'Student:%') AS student,
               count(*) FILTER (WHERE a.content LIKE '%' || chr(10) || 'Counsellor%') AS counsellor
        FROM crm_activities a JOIN crm_leads l ON l.lead_id = a.lead_id
        WHERE l.is_archived = false{scope} AND a.activity_type = :bot AND a.content LIKE :message AND a.created_at >= :month
    """), {**params, "bot": BOT_ACTIVITY_TYPE, "message": MESSAGE_PREFIX, "month": _now() - timedelta(days=30)})).mappings().first()
    messages = {"student": int((counts or {}).get("student") or 0), "counsellor": int((counts or {}).get("counsellor") or 0)}
    return summarize([dict(row) for row in rows], messages)


def context_line(bot: dict) -> str:
    reply = f"median first logged reply {bot['median_reply_min']} min over {bot['reply_sample']} leads" if bot["median_reply_min"] is not None else "no logged replies yet"
    return (f"WHATSAPP BOT (live CRM, handovers only; students who drop out in the bot are not included): {bot['total']} handed over, "
            f"{bot['month']} in 30 days, {bot['new_leads']} new and {bot['merged']} merged into existing leads, {bot['enrolled']} enrolled "
            f"({bot['conversion']}%), {bot['unassigned']} unassigned, {bot['waiting']} new leads with no logged reply, {reply}. "
            "Replies from a counsellor's own phone are not visible unless logged.")
