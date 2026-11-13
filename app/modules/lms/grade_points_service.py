"""Points for released Distinction results: 100 per assessment, taken back if the result changes or is unreleased."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.lms import points_ledger

DISTINCTION_POINTS = 100
SOURCE = "grade_distinction"
PREFIX = "grade"
WITHDRAWN = "Distinction result changed or withdrawn"


def plan_changes(current: dict[int, str], entries: list[tuple[str, int]], student_user_id: int) -> list[dict]:
    """`current` maps assignment id to its title for every released Distinction the student has."""
    rows = points_ledger.plan({key: f"Distinction: {title}" for key, title in current.items()}, entries, student_user_id, PREFIX, DISTINCTION_POINTS, WITHDRAWN)
    return [{"assignment_id": row["item"], "points": row["points"], "reason": row["reason"], "event_key": row["event_key"]} for row in rows]


async def sync_distinction_points(db: AsyncSession, student_user_id: int, current: dict[int, str]) -> int:
    """Brings the student's Distinction points in line with their released results. Returns the net points. The caller commits."""
    return await points_ledger.sync(
        db, student_user_id, source=SOURCE, prefix=PREFIX, amount=DISTINCTION_POINTS,
        current={key: f"Distinction: {title}" for key, title in current.items()}, withdrawn_reason=WITHDRAWN,
    )
