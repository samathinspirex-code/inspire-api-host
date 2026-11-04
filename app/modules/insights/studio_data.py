"""CRM queries behind the Data Studio panels. Every figure here is measured, never modelled."""
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.insights.scoring import heat, hours_since, score_lead, source_label, win_probability

OUTCOMES = [("Uncontactable", {"uncontactable"}), ("Future Prospect", {"future_prospect"}), ("Not interested", {"not_interested"}),
            ("In progress", {"new_lead", "contacted", "follow_up", "potential"}), ("Enrolled", {"enrolled"})]
MAX_SOURCES = 7
# Channels whose leads the CRM already records. Their spend is unknown until an ad account is connected.
LIVE_CHANNELS = {"WhatsApp Business": ("whatsapp",), "Website & SEO": ("website_admission", "website_contact", "wtc_registrations_form")}


def _scope(counsellor_id: int | None) -> tuple[str, dict]:
    if counsellor_id is None:
        return "", {}
    return " AND l.assigned_counsellor_id = :cid", {"cid": counsellor_id}


def _outcome(stage: str | None) -> str:
    return next((name for name, stages in OUTCOMES if stage in stages), "In progress")


def build_flow(rows: list[tuple[str | None, str | None, int]]) -> dict:
    """Source -> outcome, from (source, stage, count) rows. Smaller sources fold into Other."""
    totals: dict[str, int] = {}
    for source, _, count in rows:
        label = source_label(source)
        totals[label] = totals.get(label, 0) + count
    ranked = sorted(totals, key=lambda name: -totals[name])
    keep = set(ranked[:MAX_SOURCES - 1] if len(ranked) > MAX_SOURCES else ranked)
    flows: dict[str, dict[str, int]] = {}
    for source, stage, count in rows:
        label = source_label(source)
        label = label if label in keep else "Other"
        bucket = flows.setdefault(label, {name: 0 for name, _ in OUTCOMES})
        bucket[_outcome(stage)] += count
    sources = sorted(flows.items(), key=lambda item: -sum(item[1].values()))
    return {
        "total": sum(count for *_, count in rows),
        "outcomes": [{"name": name, "total": sum(values[name] for _, values in sources)} for name, _ in OUTCOMES],
        "sources": [{"name": name, "total": sum(values.values()), "flows": [values[outcome] for outcome, _ in OUTCOMES]} for name, values in sources],
    }


async def channel_counts(db: AsyncSession, counsellor_id: int | None) -> dict[str, dict]:
    scope, params = _scope(counsellor_id)
    result = {}
    for name, sources in LIVE_CHANNELS.items():
        row = (await db.execute(text(f"""
            SELECT count(*) FILTER (WHERE l.created_at >= now() - interval '30 days') AS leads,
                   count(*) FILTER (WHERE l.created_at >= now() - interval '30 days' AND l.stage = 'enrolled') AS enrolled,
                   coalesce(array_agg((now()::date - l.created_at::date) / 7) FILTER (WHERE l.created_at >= now() - interval '56 days'), '{{}}') AS weeks
            FROM crm_leads l WHERE l.is_archived = false{scope} AND l.source = ANY(:sources)
        """), {**params, "sources": list(sources)})).mappings().first()
        trend = [0] * 8
        for week in row["weeks"]:
            if 0 <= week < 8:
                trend[7 - week] += 1
        result[name] = {"spend": 0, "leads": int(row["leads"]), "enrolled": int(row["enrolled"]), "source": "cms", "live": True, "trend": trend}
    return result


async def source_flow(db: AsyncSession, counsellor_id: int | None) -> dict:
    scope, params = _scope(counsellor_id)
    rows = (await db.execute(text(
        f"SELECT l.source, l.stage, count(*) FROM crm_leads l WHERE l.is_archived = false{scope} GROUP BY l.source, l.stage"
    ), params)).all()
    return build_flow([(row[0], row[1], int(row[2])) for row in rows])


async def source_programme(db: AsyncSession, counsellor_id: int | None) -> dict:
    scope, params = _scope(counsellor_id)
    rows = (await db.execute(text(
        "SELECT l.source, coalesce(nullif(l.interested_course, ''), nullif(l.interested_programme, ''), 'Unspecified'), count(*) "
        f"FROM crm_leads l WHERE l.is_archived = false{scope} GROUP BY 1, 2"
    ), params)).all()
    programme_totals: dict[str, int] = {}
    source_totals: dict[str, int] = {}
    for source, programme, count in rows:
        programme_totals[programme] = programme_totals.get(programme, 0) + int(count)
        label = source_label(source)
        source_totals[label] = source_totals.get(label, 0) + int(count)
    programmes = sorted(programme_totals, key=lambda name: -programme_totals[name])[:6]
    sources = sorted(source_totals, key=lambda name: -source_totals[name])[:10]
    cells = {source: [0] * len(programmes) for source in sources}
    for source, programme, count in rows:
        label = source_label(source)
        if label in cells and programme in programmes:
            cells[label][programmes.index(programme)] += int(count)
    return {"programmes": programmes, "rows": [{"source": source, "cells": cells[source]} for source in sources]}


async def counsellor_source(db: AsyncSession, counsellor_id: int | None) -> dict:
    scope, params = _scope(counsellor_id)
    rows = (await db.execute(text(
        "SELECT coalesce(nullif(l.counsellor_name, ''), 'Unassigned'), l.source, count(*), count(*) FILTER (WHERE l.stage = 'enrolled') "
        f"FROM crm_leads l WHERE l.is_archived = false AND l.assigned_counsellor_id IS NOT NULL{scope} GROUP BY 1, 2"
    ), params)).all()
    source_totals: dict[str, int] = {}
    person_totals: dict[str, int] = {}
    data: dict[tuple[str, str], list[int]] = {}
    for person, source, leads, enrolled in rows:
        label = source_label(source)
        source_totals[label] = source_totals.get(label, 0) + int(leads)
        person_totals[person] = person_totals.get(person, 0) + int(leads)
        entry = data.setdefault((person, label), [0, 0])
        entry[0] += int(leads)
        entry[1] += int(enrolled)
    sources = sorted(source_totals, key=lambda name: -source_totals[name])[:6]
    people = sorted(person_totals, key=lambda name: -person_totals[name])[:8]
    return {"sources": sources, "rows": [
        {"name": person, "cells": [{"leads": data.get((person, source), [0, 0])[0], "enrolled": data.get((person, source), [0, 0])[1]} for source in sources]}
        for person in people
    ]}


async def whatsapp_numbers(db: AsyncSession) -> dict[int, str]:
    rows = (await db.execute(text(
        "SELECT counsellor_user_id, phone_number FROM whatsapp_lines WHERE counsellor_user_id IS NOT NULL ORDER BY created_at"
    ))).all()
    return {row[0]: row[1] for row in rows}


def mask_number(number: str | None) -> str:
    digits = (number or "").strip()
    if len(digits) < 8:
        return "—"
    return f"{digits[:6]} *** {digits[-4:]}"


async def tracked_people(db: AsyncSession, counsellor_id: int | None, query: str | None = None, limit: int = 40) -> list[dict]:
    scope, params = _scope(counsellor_id)
    search = ""
    if query and query.strip():
        search = " AND (l.full_name ILIKE :q OR l.phone ILIKE :q OR l.interested_course ILIKE :q OR l.interested_programme ILIKE :q)"
        params["q"] = f"%{query.strip()}%"
    rows = (await db.execute(text(f"""
        SELECT l.lead_id, l.full_name, l.phone, l.stage, l.source, l.counsellor_name, l.created_at, l.last_contacted_at, l.last_activity_at,
               l.followup_count, l.unanswered_calls, l.payment_plan,
               coalesce(nullif(l.interested_course, ''), nullif(l.interested_programme, ''), 'Unspecified') AS programme
        FROM crm_leads l WHERE l.is_archived = false{scope}{search}
        ORDER BY l.last_activity_at DESC NULLS LAST, l.lead_id DESC LIMIT 200
    """), params)).mappings().all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    people = []
    for row in rows:
        lead = dict(row)
        lead["last_touch"] = lead.get("last_contacted_at")
        lead["instalment"] = bool(lead.get("payment_plan"))
        value = score_lead(lead, now)
        idle = hours_since(lead["last_touch"], now)
        people.append({"lead_id": lead["lead_id"], "full_name": lead["full_name"], "phone": lead["phone"], "stage": lead["stage"],
                       "source": source_label(lead["source"]), "programme": lead["programme"], "counsellor": lead["counsellor_name"],
                       "score": value, "heat": heat(value), "win_probability": round(win_probability(lead, now) * 100, 1),
                       "idle_hours": round(idle) if idle is not None else None,
                       "momentum": value + (12 if idle is not None and idle < 48 else 0)})
    people.sort(key=lambda item: -item["momentum"])
    return people[:limit]


async def referral_summary(db: AsyncSession, counsellor_id: int | None) -> dict:
    scope, params = _scope(counsellor_id)
    funnel = (await db.execute(text(f"""
        SELECT count(*) AS leads,
               count(*) FILTER (WHERE l.stage NOT IN ('new_lead', 'uncontactable')) AS contacted,
               count(*) FILTER (WHERE l.stage IN ('follow_up', 'potential', 'enrolled')) AS counselled,
               count(*) FILTER (WHERE l.stage = 'enrolled') AS enrolled
        FROM crm_leads l WHERE l.is_archived = false AND coalesce(l.referral_code, '') <> ''{scope}
    """), params)).mappings().first() or {}
    codes = (await db.execute(text(f"""
        SELECT l.referral_code AS code, coalesce(max(p.name), l.referral_code) AS name, coalesce(max(p.partner_type), 'Referral code') AS type,
               count(*) AS leads, count(*) FILTER (WHERE l.stage = 'enrolled') AS enrolled
        FROM crm_leads l LEFT JOIN partner_links p ON p.code = l.referral_code
        WHERE l.is_archived = false AND coalesce(l.referral_code, '') <> ''{scope}
        GROUP BY l.referral_code ORDER BY enrolled DESC, leads DESC LIMIT 12
    """), params)).mappings().all()
    invites = (await db.execute(text(f"""
        SELECT l.lead_id, l.full_name, l.stage,
               coalesce(nullif(l.interested_course, ''), nullif(l.interested_programme, ''), 'Unspecified') AS programme
        FROM crm_leads l WHERE l.is_archived = false AND l.stage = 'enrolled'{scope}
        ORDER BY l.updated_at DESC NULLS LAST, l.lead_id DESC LIMIT 5
    """), params)).mappings().all()
    return {"funnel": {key: int(funnel.get(key) or 0) for key in ("leads", "contacted", "counselled", "enrolled")},
            "codes": [dict(row) for row in codes], "invites": [dict(row) for row in invites]}
