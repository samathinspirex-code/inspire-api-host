"""Aggregates the live CRM and CMS into one snapshot the studio pages share."""

import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cms import dashboard_service
from app.modules.crm.service import CONTACT_ACTIVITY_TYPES, PIPELINE_STAGES, _ensure_lead_indexes
from app.modules.insights.scoring import factors, heat, hours_since, next_action, score_lead, source_label, win_probability

CONTACT_SQL = ", ".join(f"'{item}'" for item in sorted(CONTACT_ACTIVITY_TYPES))
STAGE_LABELS = {
    "new_lead": "New Lead",
    "uncontactable": "Uncontactable",
    "contacted": "Contacted",
    "future_prospect": "Future Prospect",
    "not_interested": "Not Interested",
    "lost_to_competitor": "Lost to Competitor",
    "cant_afford": "Can't Afford",
    "enrolled": "Enrolled",
}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
PALETTE = ["#6D28D9", "#2563EB", "#0E9F6E", "#D9480F", "#DB2777", "#0891B2", "#CA8A04", "#4F46E5"]
_SNAPSHOT_TTL = 20.0
_snapshot_cache: dict[int | None, tuple[float, dict]] = {}
_snapshot_locks: dict[int | None, asyncio.Lock] = {}


def _json_rows(value):
    if value is None:
        return []
    if isinstance(value, str):
        return json.loads(value)
    return value


def _as_date(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return value


def _as_datetime(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _where(counsellor_id: int | None) -> str:
    clause = "l.is_archived = false"
    if counsellor_id is not None:
        clause += " AND l.assigned_counsellor_id = :cid"
    return clause


def _params(counsellor_id: int | None, **extra) -> dict:
    params = dict(extra)
    if counsellor_id is not None:
        params["cid"] = counsellor_id
    return params


def _color(name: str) -> str:
    return PALETTE[abs(sum(ord(char) for char in name)) % len(PALETTE)]


def _activity_text(name: str | None, content: str | None, kind: str | None, lead_name: str | None) -> str:
    text = (content or kind or "updated the lead").strip()
    if "Original workbook" in text:
        text = text.split("Original workbook", 1)[0].strip(" :.")
    brace = text.find("{")
    if brace > 0:
        text = text[:brace].strip(" :.")
    if len(text) > 160:
        text = text[:157].rsplit(" ", 1)[0] + "…"
    return f"{name or 'Team'} · {text or kind or 'updated the lead'} · {lead_name}"


def _streak(days: set, today) -> int:
    streak = 0
    cursor = today
    if cursor not in days and (cursor - timedelta(days=1)) in days:
        cursor = cursor - timedelta(days=1)
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def load_snapshot(db: AsyncSession, counsellor_id: int | None = None) -> dict:
    lock = _snapshot_locks.setdefault(counsellor_id, asyncio.Lock())
    async with lock:
        cached = _snapshot_cache.get(counsellor_id)
        if cached and time.monotonic() - cached[0] < _SNAPSHOT_TTL:
            return cached[1]
        await _ensure_lead_indexes()
        result = await _load_snapshot(db, counsellor_id)
        _snapshot_cache[counsellor_id] = (time.monotonic(), result)
        return result


async def _load_snapshot(db: AsyncSession, counsellor_id: int | None = None) -> dict:
    now = _now()
    since = now - timedelta(days=90)
    month = now - timedelta(days=30)
    where = _where(counsellor_id)
    params = _params(
        counsellor_id,
        since=since,
        month=month,
        now=now,
        hour_ago=now - timedelta(hours=1),
        day_ago=now - timedelta(days=1),
        three_ago=now - timedelta(days=3),
        week_ago=now - timedelta(days=7),
    )
    pack = (await db.execute(text(f"""
        SELECT
          (SELECT coalesce(json_agg(json_build_array(stage, cnt)), '[]'::json)
             FROM (SELECT l.stage AS stage, count(*) AS cnt FROM crm_leads l WHERE {where} GROUP BY l.stage) s) AS stages,
          (SELECT coalesce(json_agg(json_build_array(src, leads, enrolled) ORDER BY leads DESC), '[]'::json)
             FROM (
               SELECT l.source AS src, count(*) AS leads, count(*) FILTER (WHERE l.stage = 'enrolled') AS enrolled
               FROM crm_leads l WHERE {where} GROUP BY l.source
             ) s) AS sources,
          (SELECT coalesce(json_agg(json_build_array(name, leads, enrolled) ORDER BY leads DESC), '[]'::json)
             FROM (
               SELECT name, leads, enrolled FROM (
                 SELECT coalesce(nullif(l.interested_course, ''), nullif(l.interested_programme, ''), 'Unspecified') AS name,
                        count(*) AS leads, count(*) FILTER (WHERE l.stage = 'enrolled') AS enrolled
                 FROM crm_leads l WHERE {where} GROUP BY 1
               ) named ORDER BY leads DESC LIMIT 12
             ) s) AS programmes,
          (SELECT coalesce(json_agg(json_build_array(day, cnt) ORDER BY day), '[]'::json)
             FROM (
               SELECT date(l.created_at) AS day, count(*) AS cnt
               FROM crm_leads l WHERE {where} AND l.created_at >= :since GROUP BY 1
             ) s) AS daily,
          (SELECT json_build_array(
               count(*) FILTER (WHERE l.created_at > :hour_ago),
               count(*) FILTER (WHERE l.created_at <= :hour_ago AND l.created_at > :day_ago),
               count(*) FILTER (WHERE l.created_at <= :day_ago AND l.created_at > :three_ago),
               count(*) FILTER (WHERE l.created_at <= :three_ago AND l.created_at > :week_ago),
               count(*) FILTER (WHERE l.created_at <= :week_ago)
             ) FROM crm_leads l WHERE {where} AND l.stage = 'new_lead') AS aging,
          (SELECT coalesce(json_agg(json_build_array(dow, leads, enrolled)), '[]'::json)
             FROM (
               SELECT extract(dow FROM l.created_at)::int AS dow, count(*) AS leads,
                      count(*) FILTER (WHERE l.stage = 'enrolled') AS enrolled
               FROM crm_leads l WHERE {where} AND l.created_at >= :since GROUP BY 1
             ) s) AS weekday,
          (SELECT avg(extract(epoch FROM (l.enrolled_at - l.created_at)) / 86400)
             FROM crm_leads l
             WHERE {where} AND l.stage = 'enrolled' AND l.enrolled_at IS NOT NULL AND l.enrolled_at >= l.created_at) AS enrol_days,
          (SELECT avg(extract(epoch FROM (a.first_at - l.created_at)) / 86400)
             FROM crm_leads l JOIN (
               SELECT lead_id, min(created_at) AS first_at FROM crm_activities
               WHERE activity_type IN ({CONTACT_SQL}) GROUP BY lead_id
             ) a ON a.lead_id = l.lead_id
             WHERE {where} AND a.first_at >= l.created_at) AS velocity_days,
          (SELECT count(*) FROM crm_leads l WHERE {where} AND l.stage = 'enrolled' AND l.enrolled_at >= :month) AS month_enrolled,
          (SELECT count(*) FROM crm_leads l WHERE {where} AND l.created_at >= :month) AS active_month,
          (
            WITH open_leads AS (
              SELECT l.stage, coalesce(l.source, '') AS source, coalesce(l.followup_count, 0) AS followup_count,
                     (l.payment_plan IS NOT NULL AND l.payment_plan <> '') AS instalment,
                     l.assigned_counsellor_id,
                     extract(epoch FROM (:now - coalesce(l.last_contacted_at, l.last_activity_at, l.created_at))) / 3600 AS hours_idle
              FROM crm_leads l
              WHERE {where} AND l.stage NOT IN ('enrolled', 'not_interested', 'lost_to_competitor')
            ), scored AS (
              SELECT assigned_counsellor_id, hours_idle,
                least(99, greatest(1,
                  CASE stage
                    WHEN 'new_lead' THEN 16 WHEN 'uncontactable' THEN 4 WHEN 'contacted' THEN 30
                    WHEN 'future_prospect' THEN 22 WHEN 'cant_afford' THEN 14 ELSE 10
                  END
                  + least(33, followup_count * 5 + CASE WHEN followup_count > 0 THEN 8 ELSE 0 END + CASE WHEN instalment THEN 5 ELSE 0 END)
                  + CASE WHEN hours_idle < 24 THEN 12 WHEN hours_idle < 72 THEN 7 WHEN hours_idle < 168 THEN 2 ELSE 0 END
                  + CASE source
                    WHEN 'meta' THEN 2 WHEN 'meta_lead_forms' THEN 2 WHEN 'facebook_chat' THEN 2 WHEN 'instagram' THEN 2
                    WHEN 'whatsapp' THEN 5 WHEN 'website_admission' THEN 8 WHEN 'website_contact' THEN 6
                    WHEN 'wtc_registrations_form' THEN 6 WHEN 'manual' THEN 4 WHEN 'referral' THEN 12
                    WHEN 'walk_in' THEN 11 WHEN 'fair' THEN 6 WHEN 'hotline_call' THEN 4
                    WHEN 'tiktok_lead_forms' THEN 3 WHEN 'tiktok_chat' THEN 3 WHEN 'linkedin' THEN 4
                    ELSE 3
                  END
                  + CASE WHEN instalment THEN 5 ELSE 0 END
                )) AS score,
                CASE stage
                  WHEN 'new_lead' THEN 0.06 WHEN 'uncontactable' THEN 0.02 WHEN 'contacted' THEN 0.20
                  WHEN 'future_prospect' THEN 0.10 WHEN 'cant_afford' THEN 0.04 ELSE 0.05
                END AS win_base
              FROM open_leads
            )
            SELECT json_build_object(
              'hot', count(*) FILTER (WHERE score >= 62),
              'warm', count(*) FILTER (WHERE score >= 40 AND score < 62),
              'expected', coalesce(sum(least(0.95, win_base * (0.45 + score / 90.0))), 0),
              'idle', coalesce((
                SELECT json_agg(json_build_array(assigned_counsellor_id, n))
                FROM (
                  SELECT assigned_counsellor_id, count(*) AS n
                  FROM scored
                  WHERE score >= 62 AND hours_idle > 24 AND assigned_counsellor_id IS NOT NULL
                  GROUP BY assigned_counsellor_id
                ) idle_rows
              ), '[]'::json)
            )
            FROM scored
          ) AS heat_totals
    """), params)).mappings().one()

    stage_rows = _json_rows(pack["stages"])
    stages = {key: 0 for key in PIPELINE_STAGES}
    for stage, count in stage_rows:
        if stage in stages:
            stages[stage] = int(count)
    total = sum(stages.values())

    source_rows = _json_rows(pack["sources"])
    sources = [
        {"key": source or "manual", "label": source_label(source), "leads": int(leads), "enrolled": int(enrolled), "source": "cms"}
        for source, leads, enrolled in source_rows
    ]

    programme_rows = _json_rows(pack["programmes"])
    programmes = [
        {"name": name, "leads": int(leads), "enrolled": int(enrolled), "color": _color(name), "source": "cms"}
        for name, leads, enrolled in programme_rows
    ]

    by_day = {_as_date(day): int(count) for day, count in _json_rows(pack["daily"])}
    daily = []
    cursor = since.date()
    while cursor <= now.date():
        daily.append({"date": cursor.isoformat(), "count": by_day.get(cursor, 0)})
        cursor += timedelta(days=1)

    aging_row = _json_rows(pack["aging"])
    aging = [
        {"key": "<1h", "count": int(aging_row[0] or 0)},
        {"key": "1–24h", "count": int(aging_row[1] or 0)},
        {"key": "1–3d", "count": int(aging_row[2] or 0)},
        {"key": "3–7d", "count": int(aging_row[3] or 0)},
        {"key": "7d+", "count": int(aging_row[4] or 0)},
    ]
    past_sla = aging[2]["count"] + aging[3]["count"] + aging[4]["count"]

    heat_rows = (await db.execute(text(
        "SELECT extract(dow FROM a.created_at)::int, extract(hour FROM a.created_at)::int, count(*) "
        "FROM crm_activities a JOIN crm_leads l ON l.lead_id = a.lead_id "
        f"WHERE {where} AND a.activity_type IN ({CONTACT_SQL}) AND a.created_at >= :since "
        "GROUP BY 1, 2"
    ), params)).all()
    grid = [[0 for _ in range(15)] for _ in range(7)]
    peak_value = 0
    peak = "Not enough calls yet"
    heat_source = "modelled"
    if heat_rows:
        raw = [[0 for _ in range(15)] for _ in range(7)]
        busiest = (0, 0, 0)
        for dow, hour, count in heat_rows:
            slot = int(hour) - 8
            if 0 <= slot < 15:
                row = (int(dow) + 6) % 7
                raw[row][slot] += int(count)
                if raw[row][slot] > busiest[0]:
                    busiest = (raw[row][slot], row, slot)
        peak_value = max(max(row) for row in raw) or 1
        grid = [[round(value / peak_value, 3) for value in row] for row in raw]
        peak = f"{WEEKDAYS[busiest[1]]} {8 + busiest[2]}:00"
        heat_source = "modelled"
    else:
        for day in range(7):
            for hour in range(15):
                clock = 8 + hour
                evening = 0.5 * pow(2.718, -((clock - 21) ** 2) / 5)
                lunch = 0.35 * pow(2.718, -((clock - 13) ** 2) / 3)
                grid[day][hour] = round(min(1, 0.15 + evening + lunch + (0.15 if day >= 5 else 0)), 3)
        peak = "Sat 20:00"
        peak_value = 0

    heat_totals = pack["heat_totals"]
    if isinstance(heat_totals, str):
        heat_totals = json.loads(heat_totals)
    hot = int(heat_totals["hot"] or 0)
    warm = int(heat_totals["warm"] or 0)
    expected = float(heat_totals["expected"] or 0)
    hot_by_owner = {int(owner): int(count) for owner, count in _json_rows(heat_totals["idle"]) if owner}
    open_lead_count = total - stages["enrolled"] - stages["not_interested"] - stages["lost_to_competitor"]

    activity_rows = (await db.execute(text(
        "SELECT a.activity_type, a.content, a.counsellor_name, a.created_at, l.full_name "
        "FROM crm_activities a JOIN crm_leads l ON l.lead_id = a.lead_id "
        f"WHERE {where} ORDER BY a.created_at DESC LIMIT 16"
    ), params)).all()
    activity = []
    for kind, content, name, created, lead_name in activity_rows:
        elapsed = hours_since(created, now) or 0
        activity.append({
            "text": _activity_text(name, content, kind, lead_name),
            "hours_ago": round(elapsed, 1),
            "kind": kind,
            "counsellor": name or "Team",
        })

    reply_rows = (await db.execute(text(
        "SELECT l.assigned_counsellor_id, extract(epoch FROM (min(a.created_at) - l.created_at)) / 60 "
        "FROM crm_leads l JOIN crm_activities a ON a.lead_id = l.lead_id "
        f"WHERE {where} AND a.activity_type IN ({CONTACT_SQL}) AND a.created_at >= l.created_at "
        "AND l.created_at >= :month GROUP BY l.lead_id, l.assigned_counsellor_id"
    ), params)).all()
    reply_by_owner: dict[int, list[float]] = defaultdict(list)
    all_replies: list[float] = []
    for owner, minutes in reply_rows:
        if minutes is None or minutes < 0:
            continue
        all_replies.append(float(minutes))
        if owner:
            reply_by_owner[int(owner)].append(float(minutes))

    def median(values: list[float]) -> int | None:
        if not values:
            return None
        ordered = sorted(values)
        return int(round(ordered[len(ordered) // 2]))

    response_cuts = [0, 0, 0, 0, 0, 0]
    for minutes in all_replies:
        hours = minutes / 60
        if minutes < 5:
            response_cuts[0] += 1
        elif minutes < 15:
            response_cuts[1] += 1
        elif minutes < 60:
            response_cuts[2] += 1
        elif hours < 4:
            response_cuts[3] += 1
        elif hours < 24:
            response_cuts[4] += 1
        else:
            response_cuts[5] += 1
    response_total = sum(response_cuts) or 1
    response_buckets = [
        {"label": label, "pct": round(count / response_total * 100)}
        for label, count in zip(["< 5 min", "5–15", "15–60", "1–4 h", "4–24 h", "> 24 h"], response_cuts)
    ]

    day_rows = _json_rows(pack["weekday"])
    weekday = []
    for index in range(7):
        match = next((row for row in day_rows if (int(row[0]) + 6) % 7 == index), None)
        leads = int(match[1]) if match else 0
        enrolled = int(match[2]) if match else 0
        weekday.append({"day": WEEKDAYS[index], "leads": leads, "conversion": round(enrolled / leads * 100, 1) if leads else 0})

    velocity_row = pack["velocity_days"]
    enrol_row = pack["enrol_days"]
    first_days = round(float(velocity_row), 1) if velocity_row else None
    enrol_days = round(float(enrol_row), 1) if enrol_row else None
    velocity = [
        {"label": "New → Contacted", "days": first_days if first_days is not None else 1.8, "source": "modelled" if first_days is not None else "demo"},
        {"label": "Contacted → Counselled", "days": 4.6, "source": "demo"},
        {"label": "Counselled → Applied", "days": 6.2, "source": "demo"},
        {"label": "Applied → Paid", "days": max(0.5, round((enrol_days or 12) - (first_days or 1.8) - 10.8, 1)) if enrol_days else 5.1, "source": "modelled" if enrol_days else "demo"},
    ]

    month_enrolled = int(pack["month_enrolled"] or 0)
    active_month = int(pack["active_month"] or 0)

    counsellor_rows = (await db.execute(text(
        "SELECT u.user_id, coalesce(nullif(u.full_name, ''), u.email), "
        "count(l.lead_id) FILTER (WHERE l.assigned_at >= :month), "
        "count(l.lead_id) FILTER (WHERE l.stage = 'enrolled' AND l.enrolled_at >= :month) "
        "FROM users u "
        "JOIN user_access_levels ual ON ual.user_id = u.user_id "
        "JOIN access_levels al ON al.access_level_id = ual.access_level_id AND al.access_key = 'COUNSELLOR' "
        "LEFT JOIN crm_leads l ON l.assigned_counsellor_id = u.user_id AND l.is_archived = false "
        "WHERE u.is_active = true "
        + ("AND u.user_id = :cid " if counsellor_id is not None else "")
        + "GROUP BY u.user_id, u.full_name, u.email ORDER BY u.full_name"
    ), params)).all()
    contact_rows = (await db.execute(text(
        "SELECT a.counsellor_id, count(DISTINCT a.lead_id) FROM crm_activities a "
        f"WHERE a.created_at >= :month AND a.activity_type IN ({CONTACT_SQL}) AND a.counsellor_id IS NOT NULL "
        "GROUP BY a.counsellor_id"
    ), {"month": month})).all()
    contacted = {int(owner): int(count) for owner, count in contact_rows if owner}
    day_sets: dict[int, set] = defaultdict(set)
    streak_rows = (await db.execute(text(
        "SELECT counsellor_id, date(created_at) FROM crm_activities "
        "WHERE counsellor_id IS NOT NULL AND created_at >= :since GROUP BY 1, 2"
    ), {"since": since})).all()
    for owner, day in streak_rows:
        day_sets[int(owner)].add(day)

    counsellors = []
    for user_id, name, assigned, enrolled in counsellor_rows:
        owner = int(user_id)
        assigned_n = int(assigned or 0)
        contacted_n = contacted.get(owner, 0)
        enrolled_n = int(enrolled or 0)
        streak = _streak(day_sets.get(owner, set()), now.date())
        reply = median(reply_by_owner.get(owner, []))
        counsellors.append({
            "user_id": owner,
            "name": name,
            "color": _color(name or str(owner)),
            "assigned": assigned_n,
            "contacted": contacted_n,
            "enrolled": enrolled_n,
            "streak": streak,
            "median_reply_min": reply,
            "hot_idle": hot_by_owner.get(owner, 0),
            "contact_rate": round(contacted_n / assigned_n, 3) if assigned_n else 0,
            "conversion": round(enrolled_n / assigned_n, 3) if assigned_n else 0,
        })

    cms = await dashboard_service.get_dashboard(db)
    published = cms.published_by_type or {}
    referral = next((item for item in sources if item["key"] == "referral"), None)
    meta = next((item for item in sources if item["key"] in {"meta", "meta_lead_forms"}), None)

    return {
        "generated_at": now.isoformat() + "Z",
        "stages": [{"key": key, "label": STAGE_LABELS[key], "count": stages[key]} for key in PIPELINE_STAGES],
        "stage_map": stages,
        "total": total,
        "awaiting": stages["new_lead"],
        "in_progress": stages["uncontactable"] + stages["contacted"] + stages["future_prospect"],
        "enrolled": stages["enrolled"],
        "uncontactable": stages["uncontactable"],
        "contacted": stages["contacted"],
        "cant_afford": stages["cant_afford"],
        "active_month": active_month,
        "month_enrolled": month_enrolled,
        "sources": sources,
        "programmes": programmes,
        "daily": daily,
        "aging": aging,
        "past_sla": past_sla,
        "heatmap": grid,
        "heatmap_source": heat_source,
        "peak": peak,
        "peak_calls": peak_value,
        "hot": hot,
        "warm": warm,
        "open_leads": open_lead_count,
        "expected_enrolments": round(expected, 1),
        "activity": activity,
        "counsellors": counsellors,
        "students": int(cms.total_students or 0),
        "programs": int(cms.total_programs or 0),
        "published": int(cms.published_content or 0),
        "published_by_type": {key: int(value) for key, value in published.items()},
        "draft": int(cms.draft_content or 0),
        "top_interest": {"name": programmes[0]["name"], "count": programmes[0]["leads"]} if programmes else {"name": "—", "count": 0},
        "conversion": round(stages["enrolled"] / total * 100, 2) if total else 0,
        "response_buckets": response_buckets,
        "response_source": "modelled" if all_replies else "demo",
        "velocity": velocity,
        "weekday": weekday,
        "referral_rate": round(referral["enrolled"] / referral["leads"] * 100, 1) if referral and referral["leads"] else 0,
        "meta_rate": round(meta["enrolled"] / meta["leads"] * 100, 1) if meta and meta["leads"] else 0,
        "median_reply_min": median(all_replies),
    }


async def lead_journey(db: AsyncSession, lead_id: int) -> dict | None:
    row = (await db.execute(text(
        "SELECT lead_id, full_name, phone, stage, source, created_at, last_contacted_at, last_activity_at, "
        "followup_count, payment_plan, interested_course, interested_programme, assigned_counsellor_id, counsellor_name, notes "
        "FROM crm_leads WHERE lead_id = :id"
    ), {"id": lead_id})).mappings().first()
    if not row:
        return None
    now = _now()
    lead = dict(row)
    lead["last_touch"] = lead.get("last_contacted_at") or lead.get("last_activity_at")
    lead["instalment"] = bool(lead.get("payment_plan"))
    activities = (await db.execute(text(
        "SELECT activity_type, content, counsellor_name, created_at FROM crm_activities "
        "WHERE lead_id = :id ORDER BY created_at DESC LIMIT 30"
    ), {"id": lead_id})).mappings().all()
    value = score_lead(lead, now)
    return {
        "lead_id": lead["lead_id"],
        "name": lead["full_name"],
        "stage": lead["stage"],
        "source": source_label(lead["source"]),
        "programme": lead["interested_course"] or lead["interested_programme"],
        "counsellor": lead["counsellor_name"],
        "score": value,
        "heat": heat(value),
        "win_probability": round(win_probability(lead, now) * 100, 1),
        "factors": factors(lead, now),
        "next_action": next_action(lead, now),
        "activities": [
            {"type": item["activity_type"], "content": item["content"], "who": item["counsellor_name"], "at": item["created_at"].isoformat() if item["created_at"] else None}
            for item in activities
        ],
    }
