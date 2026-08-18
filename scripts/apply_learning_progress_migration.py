"""Create the LMS student learning-progress table in configured PostgreSQL."""

import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "app" / "modules" / "lms" / "sql" / "learning_progress.sql"
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST != "localhost" else None,
    )
    try:
        async with connection.transaction():
            await connection.execute(sql_path.read_text(encoding="utf-8"))
        table_exists = await connection.fetchval(
            "SELECT to_regclass('public.lms_learning_progress') IS NOT NULL"
        )
    finally:
        await connection.close()
    print("Learning progress migration applied successfully.")
    print(f"Learning progress table verified: {table_exists}")


if __name__ == "__main__":
    asyncio.run(apply_migration())
