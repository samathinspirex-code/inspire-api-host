"""Keeps earned points in the shared XP and points ledger in line with what a student currently qualifies for.

Each item (a released Distinction, an attended class) earns a fixed number of points once while it qualifies.
If it stops qualifying, an opposite entry takes the points back; if it qualifies again, they are added again.
Entries are never edited, so the ledger keeps a full history. Event keys are `{prefix}:{item}:{student}:{n}`.
"""
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.lms.models import LmsXpLedger


def _item_of(event_key: str, prefix: str) -> int | None:
    parts = event_key.split(":")
    return int(parts[1]) if len(parts) >= 4 and parts[0] == prefix and parts[1].isdigit() else None


def plan(current: dict[int, str], entries: list[tuple[str, int]], student_user_id: int, prefix: str, amount: int, withdrawn_reason: str) -> list[dict]:
    """Ledger rows needed so each item's net points match whether it qualifies now. `current` maps item id to its reason text."""
    net: dict[int, int] = defaultdict(int)
    count: dict[int, int] = defaultdict(int)
    for event_key, points in entries:
        item = _item_of(event_key, prefix)
        if item is not None:
            net[item] += points
            count[item] += 1
    rows = [{"item": item, "points": amount, "reason": reason[:300]} for item, reason in current.items() if net[item] <= 0]
    rows += [{"item": item, "points": -total, "reason": withdrawn_reason} for item, total in net.items() if total > 0 and item not in current]
    return [{**row, "event_key": f"{prefix}:{row['item']}:{student_user_id}:{count[row['item']]}"} for row in rows]


async def sync(db: AsyncSession, student_user_id: int, *, source: str, prefix: str, amount: int, current: dict[int, str], withdrawn_reason: str) -> int:
    """Writes only the needed changes and returns the student's net points for this source. The caller commits."""
    entries = [(key, points) for key, points in (await db.execute(
        select(LmsXpLedger.event_key, LmsXpLedger.points).where(LmsXpLedger.student_user_id == student_user_id, LmsXpLedger.source_type == source)
    )).all()]
    changes = plan(current, entries, student_user_id, prefix, amount, withdrawn_reason)
    for change in changes:
        await db.execute(insert(LmsXpLedger).values(
            student_user_id=student_user_id, source_type=source, xp=0, points=change["points"], reason=change["reason"], event_key=change["event_key"],
        ).on_conflict_do_nothing(index_elements=["event_key"]))
    return sum(points for _, points in entries) + sum(change["points"] for change in changes)
