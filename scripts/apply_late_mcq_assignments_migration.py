"""Allow late submissions for timed MCQ assignments in PostgreSQL."""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def apply_migration():
    sql = Path(__file__).resolve().parents[1] / "app/modules/lms/sql/late_mcq_assignments.sql"
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST != "localhost" else None,
    )
    try:
        async with connection.transaction():
            await connection.execute(sql.read_text(encoding="utf-8"))
        checks = await connection.fetch("""
            SELECT conname, pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid = 'lms_coursework_assignments'::regclass
              AND contype = 'c' AND pg_get_constraintdef(oid) ILIKE '%allow_late%'
        """)
        if len(checks) != 1 or checks[0]["conname"] != "chk_lms_coursework_timed_late":
            raise RuntimeError("Timed assignment constraint verification failed")
    finally:
        await connection.close()
    print("Late MCQ assignment constraint updated successfully.")


if __name__ == "__main__":
    asyncio.run(apply_migration())
