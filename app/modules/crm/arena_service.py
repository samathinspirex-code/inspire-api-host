"""Counsellor Arena: XP, tiers, quests and badges from real CRM activity."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.modules.crm.service import CONTACT_ACTIVITY_TYPES
from app.modules.insights.service import _tier, _xp
from app.modules.insights.snapshot import load_snapshot

CONTACT_SQL = ", ".join(f"'{item}'" for item in sorted(CONTACT_ACTIVITY_TYPES))
QUESTS = [
    {"id": "speed", "icon": "⚡", "title": "Speed-to-lead", "detail": "Call 5 new leads within 15 minutes", "max": 5, "xp": 150},
    {"id": "hot", "icon": "🔥", "title": "Warm the hot list", "detail": "Touch leads that have been idle", "max": 6, "xp": 200},
    {"id": "rescue", "icon": "🛟", "title": "Can't-Afford rescue", "detail": "Work 3 Can't Afford leads today", "max": 3, "xp": 180},
    {"id": "night", "icon": "🌙", "title": "Golden hour", "detail": "Make 10 calls between 7 and 9 pm", "max": 10, "xp": 220},
    {"id": "close", "icon": "🎓", "title": "Closer", "detail": "Enrol one student today", "max": 1, "xp": 500},
]


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _progress(db: AsyncSession, user_id: int, today) -> dict[str, int]:
    start = datetime.combine(today, datetime.min.time())
    speed = (await db.execute(text(
        "SELECT count(*) FROM ("
        " SELECT l.lead_id FROM crm_leads l JOIN crm_activities a ON a.lead_id = l.lead_id "
        f" WHERE l.assigned_counsellor_id = :uid AND l.created_at >= :start AND a.activity_type IN ({CONTACT_SQL}) "
        " GROUP BY l.lead_id, l.created_at HAVING min(a.created_at) <= l.created_at + interval '15 minutes'"
        ") s"
    ), {"uid": user_id, "start": start})).scalar() or 0
    touched = (await db.execute(text(
        "SELECT count(DISTINCT a.lead_id) FROM crm_activities a "
        f"WHERE a.counsellor_id = :uid AND a.created_at >= :start AND a.activity_type IN ({CONTACT_SQL})"
    ), {"uid": user_id, "start": start})).scalar() or 0
    rescue = (await db.execute(text(
        "SELECT count(DISTINCT a.lead_id) FROM crm_activities a JOIN crm_leads l ON l.lead_id = a.lead_id "
        "WHERE a.counsellor_id = :uid AND a.created_at >= :start AND l.stage = 'cant_afford'"
    ), {"uid": user_id, "start": start})).scalar() or 0
    night = (await db.execute(text(
        "SELECT count(*) FROM crm_activities a "
        f"WHERE a.counsellor_id = :uid AND a.created_at >= :start AND a.activity_type IN ({CONTACT_SQL}) "
        "AND extract(hour FROM a.created_at) >= 19 AND extract(hour FROM a.created_at) < 21"
    ), {"uid": user_id, "start": start})).scalar() or 0
    closed = (await db.execute(text(
        "SELECT count(*) FROM crm_leads WHERE assigned_counsellor_id = :uid AND stage = 'enrolled' AND enrolled_at >= :start"
    ), {"uid": user_id, "start": start})).scalar() or 0
    return {"speed": int(speed), "hot": int(touched), "rescue": int(rescue), "night": int(night), "close": int(closed)}


async def arena(db: AsyncSession, viewer_id: int, focus_id: int | None) -> dict:
    snap = await load_snapshot(db, None)
    bonus_rows = (await db.execute(text("SELECT user_id, coalesce(sum(xp), 0) FROM crm_arena_claims GROUP BY user_id"))).all()
    bonus = {int(user_id): int(xp) for user_id, xp in bonus_rows}
    today = _now().date()
    claimed_rows = (await db.execute(text(
        "SELECT user_id, quest_id FROM crm_arena_claims WHERE claim_date = :day"
    ), {"day": today})).all()
    claimed = {(int(user_id), quest_id) for user_id, quest_id in claimed_rows}
    board = []
    for person in snap["counsellors"]:
        xp = _xp(person) + bonus.get(person["user_id"], 0)
        tier = _tier(xp)
        reply = person["median_reply_min"] if person["median_reply_min"] is not None else 30
        radar = [
            min(1, person["assigned"] / 60),
            person["contact_rate"],
            min(1, person["conversion"] * 12 + 0.15),
            max(0.1, 1 - (reply - 5) / 30),
            min(1, person["streak"] / 14),
        ]
        board.append({**person, "xp": xp, "tier": tier, "radar": radar})
    board.sort(key=lambda item: item["xp"], reverse=True)
    # CRM and admins see the whole team. A counsellor is locked to their own card.
    personal = focus_id is not None
    if personal:
        player = next((item for item in board if item["user_id"] == focus_id), None)
    else:
        player = board[0] if board else None
    progress = await _progress(db, player["user_id"], today) if player else {}
    extras = await _badge_counts(db, player["user_id"]) if player else {"rescued": 0, "scu": 0}
    quests = []
    for quest in QUESTS:
        value = min(quest["max"], progress.get(quest["id"], 0))
        quests.append({**quest, "value": value, "done": value >= quest["max"], "claimed": (player["user_id"], quest["id"]) in claimed if player else False})
    badges = []
    if player:
        reply = player["median_reply_min"]
        badges = [
            {"name": "First blood", "detail": "First enrolment of the period", "unlocked": player["enrolled"] > 0},
            {"name": "Speed demon", "detail": "Median reply under 10 min", "unlocked": reply is not None and reply < 10},
            {"name": "On fire", "detail": "7-day activity streak", "unlocked": player["streak"] >= 7},
            {"name": "Dialler", "detail": "Contact 15+ leads in a period", "unlocked": player["contacted"] >= 15},
            {"name": "Rescuer", "detail": "Convert a Can't Afford lead", "unlocked": extras["rescued"] > 0},
            {"name": "Pathfinder", "detail": "First SCU Masters enrolment", "unlocked": extras["scu"] > 0},
            {"name": "Mentor", "detail": "Reach Mentor tier", "unlocked": player["tier"]["index"] >= 2},
            {"name": "Legend", "detail": "Reach 8,000 XP", "unlocked": player["tier"]["index"] >= 4},
        ]
    called = (await db.execute(text(
        "SELECT count(*) FROM crm_leads WHERE is_archived = false AND stage <> 'new_lead' AND created_at >= :start"
    ), {"start": _now() - timedelta(days=7)})).scalar() or 0
    raid_total = int(called) + snap["awaiting"]
    return {
        "viewer_id": viewer_id,
        "awaiting": snap["awaiting"],
        "raid": {"cleared": int(called), "total": raid_total or 1, "reward": 2000},
        "leaderboard": board,
        "player": player,
        "quests": quests,
        "badges": badges,
        "feed": snap["activity"][:12],
        "mode": "self" if personal else "team",
        "can_claim": bool(personal and player and player["user_id"] == viewer_id),
    }


async def _badge_counts(db: AsyncSession, user_id: int) -> dict[str, int]:
    rescued = (await db.execute(text(
        "SELECT count(*) FROM crm_leads l JOIN crm_activities a ON a.lead_id = l.lead_id "
        "WHERE l.assigned_counsellor_id = :uid AND l.stage = 'enrolled' "
        "AND a.activity_type = 'stage_change' AND a.content ILIKE '%cant_afford%enrolled%'"
    ), {"uid": user_id})).scalar() or 0
    scu = (await db.execute(text(
        "SELECT count(*) FROM crm_leads l WHERE l.assigned_counsellor_id = :uid AND l.stage = 'enrolled' "
        "AND l.is_archived = false AND ("
        "  lower(coalesce(l.interested_course, '') || ' ' || coalesce(l.interested_programme, '')) LIKE '%scu%master%'"
        "  OR lower(coalesce(l.interested_course, '') || ' ' || coalesce(l.interested_programme, '')) LIKE '%master%scu%'"
        ")"
    ), {"uid": user_id})).scalar() or 0
    return {"rescued": int(rescued), "scu": int(scu)}


async def claim(db: AsyncSession, user_id: int, quest_id: str) -> dict:
    quest = next((item for item in QUESTS if item["id"] == quest_id), None)
    if not quest:
        raise ValidationError("Unknown quest")
    today = _now().date()
    progress = await _progress(db, user_id, today)
    if progress.get(quest_id, 0) < quest["max"]:
        raise ValidationError("Finish the quest before claiming it")
    await db.execute(text(
        "INSERT INTO crm_arena_claims (user_id, quest_id, claim_date, xp) VALUES (:uid, :qid, :day, :xp) "
        "ON CONFLICT (user_id, quest_id, claim_date) DO NOTHING"
    ), {"uid": user_id, "qid": quest_id, "day": today, "xp": quest["xp"]})
    await db.commit()
    return {"quest_id": quest_id, "xp": quest["xp"]}
