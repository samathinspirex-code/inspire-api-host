"""The enrolment goal shown on Command Centre. Counts stay live; this stores the target."""

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ValidationError

_ready = False


async def ensure_mission(db: AsyncSession) -> None:
    global _ready
    if _ready:
        return
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS studio_mission ("
        "id INTEGER PRIMARY KEY, "
        "target INTEGER NOT NULL, "
        "start_date DATE NOT NULL, "
        "end_date DATE NOT NULL, "
        "updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW())"
    ))
    await db.execute(text(
        "INSERT INTO studio_mission (id, target, start_date, end_date) "
        "VALUES (1, :target, :start, :end) ON CONFLICT (id) DO NOTHING"
    ), {
        "target": settings.MISSION_TARGET_ENROLMENTS,
        "start": date.fromisoformat(settings.MISSION_START_DATE),
        "end": date.fromisoformat(settings.MISSION_END_DATE),
    })
    await db.commit()
    _ready = True


async def get_mission(db: AsyncSession) -> dict:
    await ensure_mission(db)
    row = (await db.execute(text(
        "SELECT target, start_date, end_date FROM studio_mission WHERE id = 1"
    ))).one()
    return {"target": int(row[0]), "start": row[1], "end": row[2]}


async def save_mission(db: AsyncSession, target: int, start: date, end: date) -> dict:
    if target < 1:
        raise ValidationError("The goal needs at least 1 student")
    if end <= start:
        raise ValidationError("The deadline has to be after the start date")
    await ensure_mission(db)
    await db.execute(text(
        "UPDATE studio_mission SET target = :target, start_date = :start, end_date = :end, updated_at = NOW() WHERE id = 1"
    ), {"target": target, "start": start, "end": end})
    await db.commit()
    return await get_mission(db)


def milestone_plan(start: date, end: date, target: int, current: int) -> list[dict]:
    span = max(1, (end - start).days)
    points = (
        ("Phase 1", start + timedelta(days=round(span * 0.35)), 0.35),
        ("Phase 2", start + timedelta(days=round(span * 0.65)), 0.65),
        ("Final", end, 1),
    )
    plan = []
    for label, day, share in points:
        plan.append({
            "name": f"{label} · {day.strftime('%b %Y')}",
            "target": max(1, round(target * share)),
            "current": current,
        })
    return plan
