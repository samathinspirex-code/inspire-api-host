"""Counsellor Arena: season XP, tiers, levelled quests and badges from real CRM activity.

Every day boundary here is Sri Lankan. Live CRM timestamps are stored as naive UTC,
so Colombo boundaries are converted to naive UTC before they reach a query.
"""

import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.modules.crm.service import CONTACT_ACTIVITY_TYPES, REACHED_ACTIVITY_SQL
from app.modules.insights.service import _tier
from app.modules.insights.snapshot import load_snapshot

COLOMBO = ZoneInfo("Asia/Colombo")
# XP starts from zero here. Work before this moment becomes past work to claim.
SEASON_START = date(2026, 11, 4)
CONTACT_SQL = ", ".join(f"'{item}'" for item in sorted(CONTACT_ACTIVITY_TYPES))
STAGE_TARGET_SQL = "substring(a.content from 'Stage changed from ''[^'']*'' to ''([a-z_]+)''')"
FEE_REASON = "Fees Beyond Expectations"
STREAK_BONUS_STEP = 10
STREAK_BONUS_CAP = 50
RAID_QUEST_ID = "raid"
RAID_REWARD = 2000
ACTIVITY_DAYS = 126

SEASON_XP = {"assigned": 5, "reached": 40, "follow_up": 20, "future_prospect": 30, "potential": 60, "enrolled": 500}
PAST_WORK_ID = "past_work"
PAST_WORK_XP = {"reached": 2, "enrolled": 50}
REACHED_STAGES_SQL = "'contacted', 'contactable', 'follow_up', 'potential', 'future_prospect', 'not_interested', 'lost_to_competitor', 'cant_afford'"
ACTION_SQL = "(activity_type <> 'note' OR content NOT LIKE 'Lead created from source%')"

QUESTS = [
    {"id": "dialler", "icon": "📞", "title": "Dialler", "detail": "Call different leads today", "levels": [(5, 50), (10, 100), (20, 200)]},
    {"id": "conversations", "icon": "💬", "title": "Real conversations", "detail": "Connected calls with different leads", "levels": [(3, 80), (6, 160), (10, 300)]},
    {"id": "speed", "icon": "⚡", "title": "Speed-to-lead", "detail": "Call new leads within 15 minutes of assignment", "levels": [(1, 60), (3, 120), (5, 250)]},
    {"id": "hot", "icon": "🔥", "title": "Warm the hot list", "detail": "Contact Potential or Follow-Up leads idle 24h+", "levels": [(2, 60), (4, 120), (6, 240)]},
    {"id": "rescue", "icon": "🛟", "title": "Can't-Afford rescue", "detail": "Call leads who stopped over fees", "levels": [(1, 60), (2, 120), (3, 180)]},
    {"id": "night", "icon": "🌙", "title": "Golden hour", "detail": "Call different leads between 7 and 9 pm", "levels": [(2, 60), (5, 120), (10, 220)]},
    {"id": "close", "icon": "🎓", "title": "Closer", "detail": "Enrol students today", "levels": [(1, 500), (2, 300), (3, 300)]},
]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def current_season(db: AsyncSession) -> tuple[int, datetime]:
    """The running season's number and start. Season 1 has no row: it began at SEASON_START."""
    row = (await db.execute(text(
        "SELECT season_number, started_at FROM crm_arena_seasons ORDER BY season_number DESC LIMIT 1"
    ))).first()
    return (int(row[0]), row[1]) if row else (1, colombo_day_start(SEASON_START))


def colombo_today(now: datetime | None = None) -> date:
    return (now or _now()).replace(tzinfo=timezone.utc).astimezone(COLOMBO).date()


def colombo_day_start(day: date) -> datetime:
    """The naive UTC moment a Sri Lankan day begins."""
    return datetime.combine(day, time.min, COLOMBO).astimezone(timezone.utc).replace(tzinfo=None)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def arena_streak(days: set[date], today: date) -> int:
    """Consecutive active Sri Lankan days. A missed Sunday, or today so far, doesn't break it."""
    cursor = today if today in days else today - timedelta(days=1)
    streak = 0
    while True:
        if cursor in days:
            streak += 1
        elif cursor.weekday() != 6:
            return streak
        cursor -= timedelta(days=1)
        if cursor < today - timedelta(days=366):
            return streak


def streak_bonus_percent(streak: int) -> int:
    return min(STREAK_BONUS_CAP, max(0, streak) * STREAK_BONUS_STEP)


def streak_bonus_xp(base_xp: int, streak: int) -> int:
    return round(base_xp * streak_bonus_percent(streak) / 100)


def past_work_total(reached: int, enrolled: int) -> int:
    return reached * PAST_WORK_XP["reached"] + enrolled * PAST_WORK_XP["enrolled"]


def season_xp_total(counts: dict) -> int:
    return sum(int(counts.get(key, 0) or 0) * value for key, value in SEASON_XP.items()) + int(counts.get("claimed", 0) or 0)


def quest_state(quest: dict, progress: int, claimed_levels: set[int], bonus_percent: int = 0) -> dict:
    """Show a quest's levels and the next one its counsellor can claim."""
    levels = [
        {"level": index, "target": target, "xp": xp, "reached": progress >= target, "claimed": index in claimed_levels}
        for index, (target, xp) in enumerate(quest["levels"], start=1)
    ]
    upcoming = next((item for item in levels if not item["claimed"]), None)
    claimable = upcoming if upcoming and upcoming["reached"] else None
    top = quest["levels"][-1][0]
    return {
        "id": quest["id"],
        "icon": quest["icon"],
        "title": quest["title"],
        "detail": quest["detail"],
        "value": min(progress, top),
        "levels": levels,
        "target": upcoming["target"] if upcoming else top,
        "xp": upcoming["xp"] if upcoming else 0,
        "bonus_xp": round(upcoming["xp"] * bonus_percent / 100) if upcoming else 0,
        "claimable_level": claimable["level"] if claimable else None,
        "done": upcoming is None,
    }


async def _progress(db: AsyncSession, user_id: int, today: date) -> dict[str, int]:
    params = {"uid": user_id, "start": colombo_day_start(today), "end": colombo_day_start(today + timedelta(days=1))}
    calls = "a.counsellor_id = :uid AND a.activity_type = 'call' AND a.created_at >= :start AND a.created_at < :end"
    row = (await db.execute(text(f"""
        SELECT
          (SELECT count(DISTINCT a.lead_id) FROM crm_activities a WHERE {calls}) AS dialler,
          (SELECT count(DISTINCT a.lead_id) FROM crm_activities a WHERE {calls} AND a.call_outcome = 'connected') AS conversations,
          (SELECT count(*) FROM (
             SELECT l.lead_id FROM crm_leads l JOIN crm_activities a ON a.lead_id = l.lead_id AND a.activity_type = 'call'
             WHERE l.assigned_counsellor_id = :uid AND l.is_archived = false
               AND coalesce(l.assigned_at, l.created_at) >= :start AND coalesce(l.assigned_at, l.created_at) < :end
             GROUP BY l.lead_id, l.assigned_at, l.created_at
             HAVING min(a.created_at) <= coalesce(l.assigned_at, l.created_at) + interval '15 minutes'
           ) speedy) AS speed,
          (SELECT count(DISTINCT a.lead_id) FROM crm_activities a JOIN crm_leads l ON l.lead_id = a.lead_id
           WHERE a.counsellor_id = :uid AND a.created_at >= :start AND a.created_at < :end
             AND a.activity_type IN ({CONTACT_SQL}) AND {REACHED_ACTIVITY_SQL}
             AND l.stage IN ('potential', 'follow_up') AND l.created_at <= a.created_at - interval '24 hours'
             AND NOT EXISTS (
               SELECT 1 FROM crm_activities p WHERE p.lead_id = a.lead_id AND p.activity_type IN ({CONTACT_SQL})
                 AND p.created_at < a.created_at AND p.created_at >= a.created_at - interval '24 hours'
             )) AS hot,
          (SELECT count(DISTINCT a.lead_id) FROM crm_activities a JOIN crm_leads l ON l.lead_id = a.lead_id
           WHERE {calls} AND (l.stage = 'cant_afford' OR (l.stage = 'not_interested' AND l.status_reason = :fee_reason))) AS rescue,
          (SELECT count(DISTINCT a.lead_id) FROM crm_activities a
           WHERE {calls} AND extract(hour FROM a.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Colombo') IN (19, 20)) AS night,
          (SELECT count(*) FROM crm_leads l
           WHERE l.assigned_counsellor_id = :uid AND l.stage = 'enrolled' AND l.is_archived = false
             AND l.enrolled_at >= :start AND l.enrolled_at < :end) AS close
    """), {**params, "fee_reason": FEE_REASON})).mappings().one()
    return {quest["id"]: int(row[quest["id"]] or 0) for quest in QUESTS}


async def _season_counts(db: AsyncSession, user_id: int | None = None) -> dict[int, dict]:
    """Per-counsellor season totals. Each lead counts once, so moving it back and forth earns nothing."""
    number, start = await current_season(db)
    params = {"start": start, "season": number}
    only_lead = only_actor = only_claim = ""
    if user_id is not None:
        params["uid"] = user_id
        only_lead, only_actor, only_claim = (" AND l.assigned_counsellor_id = :uid", " AND a.counsellor_id = :uid", " AND user_id = :uid")
    rows = (await db.execute(text(f"""
        SELECT owner, kind, count(*) FROM (
          SELECT DISTINCT l.assigned_counsellor_id AS owner, 'assigned' AS kind, l.lead_id
          FROM crm_leads l WHERE l.is_archived = false AND l.assigned_at >= :start AND l.assigned_counsellor_id IS NOT NULL{only_lead}
          UNION
          SELECT DISTINCT l.assigned_counsellor_id, 'enrolled', l.lead_id
          FROM crm_leads l WHERE l.is_archived = false AND l.stage = 'enrolled' AND l.enrolled_at >= :start AND l.assigned_counsellor_id IS NOT NULL{only_lead}
          UNION
          SELECT DISTINCT a.counsellor_id, 'reached', a.lead_id
          FROM crm_activities a JOIN crm_leads l ON l.lead_id = a.lead_id
          WHERE l.is_archived = false AND a.counsellor_id IS NOT NULL AND a.created_at >= :start{only_actor} AND (
            (a.activity_type IN ({CONTACT_SQL}) AND {REACHED_ACTIVITY_SQL})
            OR (a.activity_type = 'stage_change' AND {STAGE_TARGET_SQL} IN ({REACHED_STAGES_SQL})))
          UNION
          SELECT DISTINCT a.counsellor_id, {STAGE_TARGET_SQL}, a.lead_id
          FROM crm_activities a JOIN crm_leads l ON l.lead_id = a.lead_id
          WHERE l.is_archived = false AND a.counsellor_id IS NOT NULL AND a.created_at >= :start{only_actor}
            AND a.activity_type = 'stage_change' AND {STAGE_TARGET_SQL} IN ('follow_up', 'future_prospect', 'potential')
        ) events GROUP BY owner, kind
    """), params)).all()
    totals: dict[int, dict] = {}
    for owner, kind, count in rows:
        totals.setdefault(int(owner), {})[kind] = int(count)
    claim_rows = (await db.execute(text(
        f"SELECT user_id, coalesce(sum(xp), 0) FROM crm_arena_claims WHERE season_number = :season{only_claim} GROUP BY user_id"
    ), params)).all()
    for owner, xp in claim_rows:
        totals.setdefault(int(owner), {})["claimed"] = int(xp)
    return totals


async def season_xp(db: AsyncSession, user_id: int) -> int:
    return season_xp_total((await _season_counts(db, user_id)).get(user_id, {}))


async def _active_days(db: AsyncSession, today: date, user_id: int | None = None) -> dict[int, dict[date, int]]:
    """Everything a counsellor did in the CRM, per Sri Lankan day."""
    params = {"since": colombo_day_start(today - timedelta(days=ACTIVITY_DAYS))}
    only = ""
    if user_id is not None:
        params["uid"] = user_id
        only = " AND counsellor_id = :uid"
    rows = (await db.execute(text(
        "SELECT counsellor_id, date(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Colombo'), count(*) FROM crm_activities "
        f"WHERE counsellor_id IS NOT NULL AND created_at >= :since AND {ACTION_SQL}{only} GROUP BY 1, 2"
    ), params)).all()
    days: dict[int, dict[date, int]] = {}
    for owner, day, count in rows:
        days.setdefault(int(owner), {})[day] = int(count)
    return days


async def _lead_totals(db: AsyncSession, start: datetime, user_id: int | None = None) -> dict[int, dict]:
    """All-time figures from each counsellor's leads. Stages are the real record: most history was never logged as calls."""
    only = " AND l.assigned_counsellor_id = :uid" if user_id is not None else ""
    params = {"start": start, **({"uid": user_id} if user_id is not None else {})}
    before_season = "coalesce(l.last_activity_at, l.created_at) < :start"
    rows = (await db.execute(text(f"""
        SELECT l.assigned_counsellor_id,
          count(*),
          count(*) FILTER (WHERE l.stage = 'new_lead'),
          count(*) FILTER (WHERE l.stage = 'uncontactable'),
          count(*) FILTER (WHERE l.stage IN ({REACHED_STAGES_SQL})),
          count(*) FILTER (WHERE l.stage = 'enrolled'),
          count(*) FILTER (WHERE l.stage IN ({REACHED_STAGES_SQL}) AND {before_season}),
          count(*) FILTER (WHERE l.stage = 'enrolled' AND {before_season})
        FROM crm_leads l
        WHERE l.is_archived = false AND l.assigned_counsellor_id IS NOT NULL{only}
        GROUP BY 1
    """), params)).all()
    totals = {}
    for owner, assigned, waiting, unreachable, reached, enrolled, past_reached, past_enrolled in rows:
        totals[int(owner)] = {
            "assigned": int(assigned), "new_waiting": int(waiting), "uncontactable": int(unreachable),
            "reached": int(reached) + int(enrolled), "enrolled": int(enrolled),
            "past_reached": int(past_reached), "past_enrolled": int(past_enrolled),
        }
    return totals


async def _past_work_claimed(db: AsyncSession, season: int, user_id: int | None = None) -> dict[int, tuple[int, int]]:
    only = " AND user_id = :uid" if user_id is not None else ""
    rows = (await db.execute(text(
        f"SELECT user_id, coalesce(sum(xp), 0), count(*) FROM crm_arena_claims WHERE quest_id = :qid AND season_number = :season{only} GROUP BY 1"
    ), {"qid": PAST_WORK_ID, "season": season, **({"uid": user_id} if user_id is not None else {})})).all()
    return {int(owner): (int(xp), int(claims)) for owner, xp, claims in rows}


def _past_work_view(totals: dict, claimed: int) -> dict:
    reached, enrolled = totals.get("past_reached", 0), totals.get("past_enrolled", 0)
    earned = past_work_total(reached, enrolled)
    return {"reached": reached, "enrolled": enrolled, "earned": earned, "claimed": claimed, "available": max(0, earned - claimed)}


async def _raid(db: AsyncSession, today: date, awaiting: int) -> dict:
    start = week_start(today)
    rows = (await db.execute(text(
        "SELECT l.assigned_counsellor_id, coalesce(nullif(u.full_name, ''), u.email), count(*) "
        "FROM crm_leads l LEFT JOIN users u ON u.user_id = l.assigned_counsellor_id "
        "WHERE l.is_archived = false AND l.stage <> 'new_lead' AND l.created_at >= :start "
        "GROUP BY 1, 2 ORDER BY 3 DESC"
    ), {"start": colombo_day_start(start)})).all()
    cleared = sum(int(row[2]) for row in rows)
    top = next((row[1] for row in rows if row[0] is not None), None)
    return {
        "cleared": cleared,
        "total": (cleared + awaiting) or 1,
        "reward": RAID_REWARD,
        "complete": cleared > 0 and awaiting == 0,
        "top_damage": top,
        "ends_in_days": (start + timedelta(days=7) - today).days,
        "week_start": start,
    }


def _public_feed_text(kind: str | None, text_value: str) -> str:
    if kind == "stage_change":
        return "enrolled a student" if "to 'enrolled'" in text_value else "moved a lead forward"
    return {
        "call": "logged a call",
        "email": "emailed a lead",
        "whatsapp": "messaged a lead",
        "counselling_session": "held a counselling session",
        "follow_up_completed": "completed a follow-up",
        "note": "added a note",
    }.get(kind or "", "updated a lead")


async def _feed(db: AsyncSession, snap: dict, board: list[dict], full_detail: bool) -> list[dict]:
    """Recent wins. Counsellors only reach their own leads, so their feed leaves out lead names and notes."""
    colors = {person["name"]: person["color"] for person in board}
    feed = []
    for item in snap["activity"]:
        who = item.get("counsellor") or "Team"
        detail = item["text"].split(" · ", 1)[1] if " · " in item["text"] else item["text"]
        feed.append({
            "who": who,
            "text": detail if full_detail else _public_feed_text(item.get("kind"), item["text"]),
            "xp": SEASON_XP["enrolled"] if item.get("kind") == "stage_change" and "to 'enrolled'" in item["text"] else 0,
            "hours_ago": item["hours_ago"],
            "color": colors.get(who),
        })
    claims = (await db.execute(text(
        "SELECT coalesce(nullif(u.full_name, ''), u.email), c.quest_id, c.xp, c.created_at "
        "FROM crm_arena_claims c JOIN users u ON u.user_id = c.user_id ORDER BY c.created_at DESC LIMIT 8"
    ))).all()
    now = _now()
    for name, quest_id, xp, created in claims:
        feed.append({
            "who": name,
            "text": {RAID_QUEST_ID: "cleared the team raid", PAST_WORK_ID: "claimed XP for past work"}.get(quest_id, "completed a daily quest"),
            "xp": int(xp),
            "hours_ago": round(max(0, (now - created).total_seconds()) / 3600, 1),
            "color": colors.get(name),
        })
    feed.sort(key=lambda item: item["hours_ago"])
    return feed[:16]


async def _badge_counts(db: AsyncSession, user_id: int) -> dict[str, int]:
    rescued = (await db.execute(text(
        "SELECT count(DISTINCT l.lead_id) FROM crm_leads l JOIN crm_activities a ON a.lead_id = l.lead_id "
        "WHERE l.assigned_counsellor_id = :uid AND l.stage = 'enrolled' AND a.activity_type = 'stage_change' "
        f"AND {STAGE_TARGET_SQL} = 'enrolled' "
        "AND (a.content LIKE 'Stage changed from ''not_interested''%' OR a.content LIKE 'Stage changed from ''cant_afford''%')"
    ), {"uid": user_id})).scalar() or 0
    scu = (await db.execute(text(
        "SELECT count(*) FROM crm_leads l WHERE l.assigned_counsellor_id = :uid AND l.stage = 'enrolled' "
        "AND l.is_archived = false AND ("
        "  lower(coalesce(l.interested_course, '') || ' ' || coalesce(l.interested_programme, '')) LIKE '%scu%master%'"
        "  OR lower(coalesce(l.interested_course, '') || ' ' || coalesce(l.interested_programme, '')) LIKE '%master%scu%'"
        ")"
    ), {"uid": user_id})).scalar() or 0
    return {"rescued": int(rescued), "scu": int(scu)}


def _badges(player: dict, extras: dict) -> list[dict]:
    return [
        {"name": "First blood", "detail": "First enrolment", "unlocked": player["enrolled"] > 0},
        {"name": "On fire", "detail": "7-day activity streak", "unlocked": player["streak"] >= 7},
        {"name": "Dialler", "detail": "Reach 15+ leads", "unlocked": player["contacted"] >= 15},
        {"name": "Rescuer", "detail": "Enrol a lead who was Not Interested", "unlocked": extras["rescued"] > 0},
        {"name": "Pathfinder", "detail": "First SCU Masters enrolment", "unlocked": extras["scu"] > 0},
        {"name": "Mentor", "detail": "Reach Mentor tier", "unlocked": player["tier"]["index"] >= 2},
        {"name": "Legend", "detail": "Reach 8,000 XP", "unlocked": player["tier"]["index"] >= 4},
    ]


async def arena(db: AsyncSession, viewer_id: int, can_claim: bool, focus_id: int | None, full_detail: bool = False) -> dict:
    """The full Arena. Only someone holding the counsellor role can claim, and only on their own card."""
    snap = await load_snapshot(db, None)
    today = colombo_today()
    season_number, season_start = await current_season(db)
    season = await _season_counts(db)
    active = await _active_days(db, today)
    totals = await _lead_totals(db, season_start)
    past_claimed = await _past_work_claimed(db, season_number)
    recent_start = today - timedelta(days=30)
    actions = {owner: sum(count for day, count in days.items() if day >= recent_start) for owner, days in active.items()}
    most_leads = max([item["assigned"] for item in totals.values()] + [1])
    most_actions = max(list(actions.values()) + [1])
    board = []
    for person in snap["counsellors"]:
        owner = person["user_id"]
        xp = season_xp_total(season.get(owner, {}))
        streak = arena_streak(set(active.get(owner, {})), today)
        own = totals.get(owner, {})
        assigned, reached, enrolled = own.get("assigned", 0), own.get("reached", 0), own.get("enrolled", 0)
        reach_rate = round(reached / assigned, 3) if assigned else 0
        conversion = round(enrolled / assigned, 3) if assigned else 0
        board.append({
            "user_id": owner,
            "name": person["name"],
            "color": person["color"],
            "assigned": assigned,
            "contacted": reached,
            "uncontactable": own.get("uncontactable", 0),
            "new_waiting": own.get("new_waiting", 0),
            "enrolled": enrolled,
            "contact_rate": reach_rate,
            "conversion": conversion,
            "actions_30d": actions.get(owner, 0),
            "streak": streak,
            "xp": xp,
            "past_work": _past_work_view(own, past_claimed.get(owner, (0, 0))[0]),
            "tier": _tier(xp),
            "radar": [
                assigned / most_leads,
                reach_rate,
                min(1, conversion * 10),
                actions.get(owner, 0) / most_actions,
                min(1, streak / 14),
            ],
        })
    board.sort(key=lambda item: (-item["xp"], -item["past_work"]["available"], -item["enrolled"]))
    for rank, person in enumerate(board, start=1):
        person["rank"] = rank
    if focus_id is not None:
        player = next((item for item in board if item["user_id"] == focus_id), None)
    else:
        player = board[0] if board else None

    raid = await _raid(db, today, snap["awaiting"])
    week = raid.pop("week_start")
    quests: list[dict] = []
    badges: list[dict] = []
    calendar: list[dict] = []
    bonus_percent = 0
    today_xp = 0
    raid_claimed = False
    if player:
        uid = player["user_id"]
        bonus_percent = streak_bonus_percent(player["streak"])
        progress = await _progress(db, uid, today)
        claim_rows = (await db.execute(text(
            "SELECT quest_id, level, claim_date, xp FROM crm_arena_claims WHERE user_id = :uid AND claim_date >= :week AND season_number = :season"
        ), {"uid": uid, "week": week, "season": season_number})).all()
        claimed_today: dict[str, set[int]] = {}
        for quest_id, level, claim_date, xp in claim_rows:
            if claim_date == today:
                claimed_today.setdefault(quest_id, set()).add(int(level))
                today_xp += int(xp)
            if quest_id == RAID_QUEST_ID and claim_date == week:
                raid_claimed = True
        quests = [
            quest_state(quest, progress.get(quest["id"], 0), claimed_today.get(quest["id"], set()), bonus_percent)
            for quest in QUESTS
        ]
        badges = _badges(player, await _badge_counts(db, uid))
        days = active.get(uid, {})
        calendar = [
            {"date": (today - timedelta(days=offset)).isoformat(), "count": days.get(today - timedelta(days=offset), 0)}
            for offset in range(ACTIVITY_DAYS - 1, -1, -1)
        ]

    tomorrow = colombo_day_start(today + timedelta(days=1))
    return {
        "viewer_id": viewer_id,
        "season": {"number": season_number, "started_at": season_start.isoformat()},
        "awaiting": snap["awaiting"],
        "raid": {**raid, "claimed": raid_claimed},
        "leaderboard": board,
        "player": player,
        "quests": quests,
        "badges": badges,
        "calendar": calendar,
        "streak_bonus_percent": bonus_percent,
        "today_xp": today_xp,
        "resets_in_seconds": max(0, int((tomorrow - _now()).total_seconds())),
        "feed": await _feed(db, snap, board, full_detail=full_detail),
        "mode": "self" if player and player["user_id"] == viewer_id else "team",
        "can_claim": bool(can_claim and player and player["user_id"] == viewer_id),
    }


async def _insert_claim(db: AsyncSession, season: int, user_id: int, quest_id: str, level: int, day: date, base_xp: int, bonus: int) -> bool:
    result = await db.execute(text(
        "INSERT INTO crm_arena_claims (season_number, user_id, quest_id, level, claim_date, xp, base_xp, streak_bonus_xp) "
        "VALUES (:season, :uid, :qid, :level, :day, :xp, :base, :bonus) "
        "ON CONFLICT (season_number, user_id, quest_id, level, claim_date) DO NOTHING RETURNING claim_id"
    ), {"season": season, "uid": user_id, "qid": quest_id, "level": level, "day": day, "xp": base_xp + bonus, "base": base_xp, "bonus": bonus})
    return result.scalar() is not None


async def claim(db: AsyncSession, user_id: int, quest_id: str, level: int = 1) -> dict:
    today = colombo_today()
    if quest_id == RAID_QUEST_ID:
        return await _claim_raid(db, user_id, today)
    if quest_id == PAST_WORK_ID:
        return await _claim_past_work(db, user_id, today)
    quest = next((item for item in QUESTS if item["id"] == quest_id), None)
    if not quest:
        raise ValidationError("Unknown quest")
    if level < 1 or level > len(quest["levels"]):
        raise ValidationError("Unknown quest level")
    target, base_xp = quest["levels"][level - 1]
    progress = await _progress(db, user_id, today)
    if progress.get(quest_id, 0) < target:
        raise ValidationError("Finish this level before claiming it")
    days = (await _active_days(db, today, user_id)).get(user_id, {})
    streak = arena_streak(set(days), today)
    bonus = streak_bonus_xp(base_xp, streak)
    number, _ = await current_season(db)
    inserted = await _insert_claim(db, number, user_id, quest_id, level, today, base_xp, bonus)
    if not inserted:
        await db.rollback()
        raise ValidationError("You already claimed this level today")
    await db.commit()
    return {"quest_id": quest_id, "level": level, "base_xp": base_xp, "streak_bonus_xp": bonus, "xp": base_xp + bonus}


async def _claim_past_work(db: AsyncSession, user_id: int, today: date) -> dict:
    number, start = await current_season(db)
    own = (await _lead_totals(db, start, user_id)).get(user_id, {})
    claimed, claims = (await _past_work_claimed(db, number, user_id)).get(user_id, (0, 0))
    available = _past_work_view(own, claimed)["available"]
    if available <= 0:
        raise ValidationError("There's no past work left to claim")
    if not await _insert_claim(db, number, user_id, PAST_WORK_ID, claims + 1, today, available, 0):
        await db.rollback()
        raise ValidationError("You already claimed this past work")
    await db.commit()
    return {"quest_id": PAST_WORK_ID, "level": claims + 1, "base_xp": available, "streak_bonus_xp": 0, "xp": available}


async def _claim_raid(db: AsyncSession, user_id: int, today: date) -> dict:
    awaiting = (await db.execute(text(
        "SELECT count(*) FROM crm_leads WHERE is_archived = false AND stage = 'new_lead'"
    ))).scalar() or 0
    raid = await _raid(db, today, int(awaiting))
    if not raid["complete"]:
        raise ValidationError("The team raid isn't cleared yet")
    number, _ = await current_season(db)
    if not await _insert_claim(db, number, user_id, RAID_QUEST_ID, 1, raid["week_start"], RAID_REWARD, 0):
        await db.rollback()
        raise ValidationError("You already claimed this week's raid")
    await db.commit()
    return {"quest_id": RAID_QUEST_ID, "level": 1, "base_xp": RAID_REWARD, "streak_bonus_xp": 0, "xp": RAID_REWARD}


async def reset_season(db: AsyncSession, admin_id: int) -> dict:
    """Start a new season for everyone. Nothing is deleted: old claims stay under their season, and final standings are kept."""
    number, _ = await current_season(db)
    totals = await _season_counts(db)
    names = dict((await db.execute(text(
        "SELECT user_id, coalesce(nullif(full_name, ''), email) FROM users WHERE user_id = ANY(:ids)"
    ), {"ids": list(totals)})).all()) if totals else {}
    standings = sorted(
        ({"user_id": owner, "name": names.get(owner), "xp": season_xp_total(counts), "tier": _tier(season_xp_total(counts))["name"]}
         for owner, counts in totals.items() if season_xp_total(counts) > 0 and owner in names),
        key=lambda item: -item["xp"],
    )
    started = _now()
    created = (await db.execute(text(
        "INSERT INTO crm_arena_seasons (season_number, started_at, started_by, previous_standings) "
        "VALUES (:number, :started, :admin, CAST(:standings AS jsonb)) "
        "ON CONFLICT (season_number) DO NOTHING RETURNING season_number"
    ), {"number": number + 1, "started": started, "admin": admin_id, "standings": json.dumps(standings)})).scalar()
    if created is None:
        await db.rollback()
        raise ValidationError("Someone else just started a new season. Refresh the page.")
    await db.commit()
    return {"number": number + 1, "started_at": started.isoformat(), "previous_standings": standings}


async def list_seasons(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(text(
        "SELECT s.season_number, s.started_at, s.previous_standings, coalesce(nullif(u.full_name, ''), u.email) "
        "FROM crm_arena_seasons s LEFT JOIN users u ON u.user_id = s.started_by ORDER BY s.season_number DESC"
    ))).all()
    return [
        {"number": int(number), "started_at": started.isoformat(), "started_by": name, "previous_standings": standings if isinstance(standings, list) else json.loads(standings or "[]")}
        for number, started, standings, name in rows
    ]
