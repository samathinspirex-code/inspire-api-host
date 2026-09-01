"""Read-only smoke check for analytics queries against configured LMS data."""
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.modules.lms.analytics_service import get_dashboard  # noqa: E402
from app.modules.lms.models import LecturerProfile, StudentProfile  # noqa: E402


async def check() -> None:
    verified = []
    async with AsyncSessionLocal() as db:
        for model, role in ((StudentProfile, "STUDENT"), (LecturerProfile, "LECTURER")):
            user_id = await db.scalar(select(model.user_id).limit(1))
            if user_id is None:
                continue
            result = await get_dashboard(db, user_id, role)
            verified.append(f"{role.lower()}={len(result.course_insights)} courses/{result.engagement_score} engagement")
        admin = await get_dashboard(db, 0, "ADMIN")
        verified.append(f"admin={len(admin.course_insights)} courses/{admin.engagement_score} engagement")
    print("Analytics queries verified" + (f" ({', '.join(verified)})" if verified else " (no profiles found)"))


if __name__ == "__main__":
    asyncio.run(check())
