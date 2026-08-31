"""Read-only smoke check for profile response queries against the configured database."""
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.modules.lms.models import LecturerProfile, StudentProfile  # noqa: E402
from app.modules.lms.profile_service import get_my_profile  # noqa: E402


async def check() -> None:
    checked: list[str] = []
    async with AsyncSessionLocal() as db:
        for model, role in ((StudentProfile, "STUDENT"), (LecturerProfile, "LECTURER")):
            user_id = await db.scalar(select(model.user_id).limit(1))
            if user_id is None:
                continue
            response = await get_my_profile(db, user_id, role)
            checked.append(
                f"{role.lower()}: {response.profile_completeness}% complete, "
                f"{response.statistics.courses} courses"
            )
    print("Profile queries verified" + (f" ({'; '.join(checked)})" if checked else " (no profiles found)"))


if __name__ == "__main__":
    asyncio.run(check())
