"""Release emails left behind by the previous LMS profile-only delete behavior."""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def main() -> None:
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        raise SystemExit("Usage: python scripts/repair_deleted_lms_accounts.py email@example.com")
    email = sys.argv[1].strip().lower()
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        result = await connection.execute("""
            UPDATE users AS u
            SET email = 'deleted+' || u.user_id || '@deleted.inspire.college',
                full_name = 'Deleted LMS user ' || u.user_id,
                is_active = FALSE
            WHERE NOT EXISTS (SELECT 1 FROM lms_student_profiles s WHERE s.user_id = u.user_id)
              AND NOT EXISTS (SELECT 1 FROM lms_lecturer_profiles l WHERE l.user_id = u.user_id)
              AND NOT EXISTS (SELECT 1 FROM user_access_levels a WHERE a.user_id = u.user_id)
              AND lower(u.email) = $1
              AND u.email NOT LIKE 'deleted+%@deleted.inspire.college'
        """, email)
    finally:
        await connection.close()
    print(f"Released legacy deleted LMS accounts: {result}")


if __name__ == "__main__":
    asyncio.run(main())
