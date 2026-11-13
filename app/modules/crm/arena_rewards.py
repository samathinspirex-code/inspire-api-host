"""Counsellor Arena rewards: admins manage vouchers and codes, counsellors collect them."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.modules.crm.arena_schemas import ArenaAwardIn, ArenaDecisionIn, ArenaRewardCodesIn, ArenaRewardIn
from app.modules.crm.arena_service import colombo_today, current_season, season_xp
from app.modules.insights.service import TIERS, _tier

REWARD_COLUMNS = "reward_id, reward_type, brand, title, value_label, description, tier_index, is_active"
# These reward types can't be handed over without something to redeem.
CODED_TYPES = ("voucher", "code")


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def _reward_values(payload: ArenaRewardIn) -> dict:
    title = payload.title.strip()
    if not title:
        raise ValidationError("Enter a reward title")
    return {
        "reward_type": payload.reward_type,
        "brand": _clean(payload.brand),
        "title": title,
        "value_label": _clean(payload.value_label),
        "description": _clean(payload.description),
        "tier_index": payload.tier_index,
        "is_active": payload.is_active,
    }


def _tier_name(tier_index: int | None) -> str | None:
    return TIERS[tier_index][0] if tier_index is not None and 0 <= tier_index < len(TIERS) else None


async def list_rewards(db: AsyncSession, start=None, end=None) -> list[dict]:
    rewards = (await db.execute(text(
        f"SELECT {REWARD_COLUMNS} FROM crm_arena_rewards ORDER BY is_active DESC, tier_index NULLS LAST, title"
    ))).mappings().all()
    code_rows = (await db.execute(text(
        "SELECT code_id, reward_id, code, expires_at, status FROM crm_arena_reward_codes ORDER BY code_id"
    ))).mappings().all()
    today = colombo_today()
    period_issued = {}
    if start is not None and end is not None:
        issued_rows = (await db.execute(text(
            "SELECT reward_id, count(*) FROM crm_arena_reward_collections "
            "WHERE status = 'given' AND decided_at >= :start AND decided_at < :end GROUP BY reward_id"
        ), {"start": start, "end": end})).all()
        period_issued = {int(reward_id): int(count) for reward_id, count in issued_rows}
    codes: dict[int, list[dict]] = {}
    for row in code_rows:
        expired = row["expires_at"] is not None and row["expires_at"] < today
        codes.setdefault(row["reward_id"], []).append({**row, "expired": expired})
    result = []
    for reward in rewards:
        own = codes.get(reward["reward_id"], [])
        result.append({
            **reward,
            "tier_name": _tier_name(reward["tier_index"]),
            "stock": sum(1 for code in own if code["status"] == "available" and not code["expired"]),
            "issued": period_issued.get(reward["reward_id"], 0) if start is not None else sum(1 for code in own if code["status"] == "issued"),
            "codes": [code for code in own if code["status"] != "void"],
        })
    return result


async def create_reward(db: AsyncSession, payload: ArenaRewardIn, admin_id: int) -> dict:
    row = (await db.execute(text(
        "INSERT INTO crm_arena_rewards (reward_type, brand, title, value_label, description, tier_index, is_active, created_by) "
        "VALUES (:reward_type, :brand, :title, :value_label, :description, :tier_index, :is_active, :admin) "
        f"RETURNING {REWARD_COLUMNS}"
    ), {**_reward_values(payload), "admin": admin_id})).mappings().one()
    await db.commit()
    return dict(row)


async def update_reward(db: AsyncSession, reward_id: int, payload: ArenaRewardIn) -> dict:
    row = (await db.execute(text(
        "UPDATE crm_arena_rewards SET reward_type = :reward_type, brand = :brand, title = :title, value_label = :value_label, "
        "description = :description, tier_index = :tier_index, is_active = :is_active, updated_at = now() "
        f"WHERE reward_id = :id RETURNING {REWARD_COLUMNS}"
    ), {**_reward_values(payload), "id": reward_id})).mappings().first()
    if row is None:
        raise NotFoundError("Reward not found")
    await db.commit()
    return dict(row)


async def add_codes(db: AsyncSession, reward_id: int, payload: ArenaRewardCodesIn, admin_id: int) -> dict:
    exists = await db.scalar(text("SELECT 1 FROM crm_arena_rewards WHERE reward_id = :id"), {"id": reward_id})
    if not exists:
        raise NotFoundError("Reward not found")
    codes = list(dict.fromkeys(code.strip() for code in payload.codes if code.strip()))
    if not codes:
        raise ValidationError("Enter at least one code")
    if any(len(code) > 160 for code in codes):
        raise ValidationError("A code can't be longer than 160 characters")
    added = 0
    for code in codes:
        result = await db.execute(text(
            "INSERT INTO crm_arena_reward_codes (reward_id, code, expires_at, created_by) "
            "VALUES (:reward, :code, :expires, :admin) ON CONFLICT (reward_id, code) DO NOTHING RETURNING code_id"
        ), {"reward": reward_id, "code": code, "expires": payload.expires_at, "admin": admin_id})
        if result.scalar() is not None:
            added += 1
    await db.commit()
    return {"added": added, "skipped": len(codes) - added}


async def void_code(db: AsyncSession, code_id: int) -> None:
    result = await db.execute(text(
        "UPDATE crm_arena_reward_codes SET status = 'void' WHERE code_id = :id AND status = 'available' RETURNING code_id"
    ), {"id": code_id})
    if result.scalar() is None:
        raise ValidationError("Only an unused code can be voided")
    await db.commit()


async def _take_code(db: AsyncSession, reward: dict, typed: str | None) -> tuple[int | None, str | None]:
    """Pick what to hand over. SKIP LOCKED stops two admins issuing the same code."""
    if typed:
        return None, typed
    row = (await db.execute(text(
        "SELECT code_id, code FROM crm_arena_reward_codes "
        "WHERE reward_id = :reward AND status = 'available' AND (expires_at IS NULL OR expires_at >= :today) "
        "ORDER BY expires_at NULLS LAST, code_id LIMIT 1 FOR UPDATE SKIP LOCKED"
    ), {"reward": reward["reward_id"], "today": colombo_today()})).first()
    if row is None:
        if reward["reward_type"] in CODED_TYPES:
            raise ValidationError(f"No codes left for {reward['title']}. Add more codes or type one in.")
        return None, None
    await db.execute(text("UPDATE crm_arena_reward_codes SET status = 'issued' WHERE code_id = :id"), {"id": row[0]})
    return int(row[0]), row[1]


async def _get_reward(db: AsyncSession, reward_id: int) -> dict:
    row = (await db.execute(text(
        f"SELECT {REWARD_COLUMNS} FROM crm_arena_rewards WHERE reward_id = :id"
    ), {"id": reward_id})).mappings().first()
    if row is None:
        raise NotFoundError("Reward not found")
    return dict(row)


async def my_rewards(db: AsyncSession, user_id: int) -> list[dict]:
    """A counsellor's rewards. Codes appear only here, and only on rewards already given to them."""
    tier = _tier(await season_xp(db, user_id))
    season, _ = await current_season(db)
    rewards = (await db.execute(text(
        f"SELECT {REWARD_COLUMNS} FROM crm_arena_rewards WHERE is_active = true AND tier_index IS NOT NULL ORDER BY tier_index, title"
    ))).mappings().all()
    collections = (await db.execute(text(
        f"SELECT c.collection_id, c.season_number, c.source, c.status, c.issued_code, c.note, c.requested_at, c.decided_at, "
        f"r.{REWARD_COLUMNS.replace(', ', ', r.')}, ca.expires_at "
        "FROM crm_arena_reward_collections c JOIN crm_arena_rewards r ON r.reward_id = c.reward_id "
        "LEFT JOIN crm_arena_reward_codes ca ON ca.code_id = c.code_id "
        "WHERE c.user_id = :uid ORDER BY c.requested_at DESC"
    ), {"uid": user_id})).mappings().all()
    tier_collections = {row["reward_id"]: row for row in collections if row["source"] == "tier" and row["season_number"] == season}
    result = []
    for reward in rewards:
        collection = tier_collections.get(reward["reward_id"])
        if collection:
            continue
        unlocked = tier["index"] >= reward["tier_index"]
        result.append({**reward, "tier_name": _tier_name(reward["tier_index"]), "state": "available" if unlocked else "locked"})
    for row in collections:
        result.append({**row, "tier_name": _tier_name(row["tier_index"]), "state": row["status"], "code": row["issued_code"] if row["status"] == "given" else None})
    for item in result:
        item.pop("issued_code", None)
    return result


async def collect(db: AsyncSession, user_id: int, reward_id: int) -> dict:
    reward = await _get_reward(db, reward_id)
    if not reward["is_active"] or reward["tier_index"] is None:
        raise ValidationError("This reward can't be collected")
    if _tier(await season_xp(db, user_id))["index"] < reward["tier_index"]:
        raise ValidationError(f"Reach {_tier_name(reward['tier_index'])} tier to collect this reward")
    season, _ = await current_season(db)
    collection_id = (await db.execute(text(
        "INSERT INTO crm_arena_reward_collections (season_number, reward_id, user_id, source, status) "
        "VALUES (:season, :reward, :uid, 'tier', 'requested') "
        "ON CONFLICT (season_number, reward_id, user_id) WHERE source = 'tier' DO NOTHING RETURNING collection_id"
    ), {"season": season, "reward": reward_id, "uid": user_id})).scalar()
    if collection_id is None:
        await db.rollback()
        raise ValidationError("You already collected this reward")
    await db.commit()
    return {"collection_id": collection_id, "state": "requested"}


async def list_collections(db: AsyncSession, start=None, end=None) -> list[dict]:
    rows = (await db.execute(text(
        "SELECT c.collection_id, c.reward_id, c.user_id, c.source, c.status, c.issued_code AS code, c.note, c.requested_at, c.decided_at, "
        "r.title, r.brand, r.value_label, r.reward_type, r.tier_index, "
        "coalesce(nullif(u.full_name, ''), u.email) AS counsellor, coalesce(nullif(d.full_name, ''), d.email) AS decided_by_name, "
        "(SELECT count(*) FROM crm_arena_reward_codes s WHERE s.reward_id = c.reward_id AND s.status = 'available' "
        " AND (s.expires_at IS NULL OR s.expires_at >= :today)) AS stock "
        "FROM crm_arena_reward_collections c "
        "JOIN crm_arena_rewards r ON r.reward_id = c.reward_id "
        "JOIN users u ON u.user_id = c.user_id "
        "LEFT JOIN users d ON d.user_id = c.decided_by "
        "WHERE (CAST(:start AS timestamp) IS NULL OR c.requested_at >= :start) AND (CAST(:end AS timestamp) IS NULL OR c.requested_at < :end) "
        "ORDER BY (c.status = 'requested') DESC, coalesce(c.decided_at, c.requested_at) DESC LIMIT 200"
    ), {"today": colombo_today(), "start": start, "end": end})).mappings().all()
    return [{**row, "tier_name": _tier_name(row["tier_index"])} for row in rows]


async def _open_collection(db: AsyncSession, collection_id: int) -> dict:
    row = (await db.execute(text(
        "SELECT collection_id, reward_id, status FROM crm_arena_reward_collections WHERE collection_id = :id FOR UPDATE"
    ), {"id": collection_id})).mappings().first()
    if row is None:
        raise NotFoundError("Reward request not found")
    if row["status"] != "requested":
        raise ValidationError("This reward request has already been decided")
    return dict(row)


async def distribute(db: AsyncSession, collection_id: int, payload: ArenaDecisionIn, admin_id: int) -> dict:
    collection = await _open_collection(db, collection_id)
    reward = await _get_reward(db, collection["reward_id"])
    code_id, code = await _take_code(db, reward, _clean(payload.code))
    await db.execute(text(
        "UPDATE crm_arena_reward_collections SET status = 'given', code_id = :code_id, issued_code = :code, note = :note, "
        "decided_by = :admin, decided_at = now() WHERE collection_id = :id"
    ), {"code_id": code_id, "code": code, "note": _clean(payload.note), "admin": admin_id, "id": collection_id})
    await db.commit()
    return {"collection_id": collection_id, "status": "given", "code": code}


async def decline(db: AsyncSession, collection_id: int, payload: ArenaDecisionIn, admin_id: int) -> dict:
    await _open_collection(db, collection_id)
    await db.execute(text(
        "UPDATE crm_arena_reward_collections SET status = 'declined', note = :note, decided_by = :admin, decided_at = now() "
        "WHERE collection_id = :id"
    ), {"note": _clean(payload.note), "admin": admin_id, "id": collection_id})
    await db.commit()
    return {"collection_id": collection_id, "status": "declined"}


async def award(db: AsyncSession, reward_id: int, payload: ArenaAwardIn, admin_id: int) -> dict:
    reward = await _get_reward(db, reward_id)
    if not reward["is_active"]:
        raise ValidationError("This reward has been retired")
    counsellor = await db.scalar(text(
        "SELECT 1 FROM users u JOIN user_access_levels ual ON ual.user_id = u.user_id "
        "JOIN access_levels al ON al.access_level_id = ual.access_level_id AND al.access_key = 'COUNSELLOR' "
        "WHERE u.user_id = :uid AND u.is_active = true"
    ), {"uid": payload.user_id})
    if not counsellor:
        raise ValidationError("Choose an active counsellor")
    code_id, code = await _take_code(db, reward, _clean(payload.code))
    season, _ = await current_season(db)
    collection_id = (await db.execute(text(
        "INSERT INTO crm_arena_reward_collections (season_number, reward_id, user_id, source, status, code_id, issued_code, note, decided_by, decided_at) "
        "VALUES (:season, :reward, :uid, 'admin_award', 'given', :code_id, :code, :note, :admin, now()) RETURNING collection_id"
    ), {"season": season, "reward": reward_id, "uid": payload.user_id, "code_id": code_id, "code": code, "note": _clean(payload.note), "admin": admin_id})).scalar()
    await db.commit()
    return {"collection_id": collection_id, "status": "given", "code": code}
