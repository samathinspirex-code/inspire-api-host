"""Create missing LMS profiles for accounts that already have STUDENT or LECTURER access.

Accounts given that access from user management before the fix had no profile row,
so they were missing from the LMS Students and Lecturers lists. Safe to run more than once:
it only inserts rows that do not exist and never changes or removes existing profiles.
"""
import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.config import settings  # noqa: E402

BACKFILLS = (
    ("STUDENT", "lms_student_profiles"),
    ("LECTURER", "lms_lecturer_profiles"),
)


async def backfill() -> None:
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        async with connection.transaction():
            for access_key, table in BACKFILLS:
                rows = await connection.fetch(
                    f"""
                    INSERT INTO {table} (user_id)
                    SELECT DISTINCT ual.user_id
                    FROM user_access_levels ual
                    JOIN access_levels al ON al.access_level_id = ual.access_level_id
                    WHERE al.access_key = $1
                      AND NOT EXISTS (SELECT 1 FROM {table} p WHERE p.user_id = ual.user_id)
                    ON CONFLICT (user_id) DO NOTHING
                    RETURNING user_id
                    """,
                    access_key,
                )
                print(f"{table}: added {len(rows)} profile(s) for user ids {[row['user_id'] for row in rows]}")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(backfill())
