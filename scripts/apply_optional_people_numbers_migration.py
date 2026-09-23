"""Allow LMS student/staff numbers to be recorded later.

Run once against the deployed database: python scripts/apply_optional_people_numbers_migration.py
"""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402


async def main() -> None:
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
            await connection.execute("ALTER TABLE lms_student_profiles ALTER COLUMN student_number DROP NOT NULL")
            await connection.execute("ALTER TABLE lms_lecturer_profiles ALTER COLUMN staff_number DROP NOT NULL")
    finally:
        await connection.close()
    print("Student and staff numbers are now optional.")


if __name__ == "__main__":
    asyncio.run(main())
