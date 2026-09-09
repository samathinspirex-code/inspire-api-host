"""Create the student password credential table in configured PostgreSQL."""

import asyncio
from pathlib import Path
import sys

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    sql_path = (
        Path(__file__).resolve().parents[1]
        / "app" / "modules" / "auth" / "sql" / "student_password.sql"
    )
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
            await connection.execute(sql_path.read_text(encoding="utf-8"))
        verified = await connection.fetchval(
            "SELECT to_regclass('public.password_credentials') IS NOT NULL"
        )
    finally:
        await connection.close()
    print(f"Student password migration applied successfully. Table verified: {verified}")


if __name__ == "__main__":
    asyncio.run(apply_migration())
