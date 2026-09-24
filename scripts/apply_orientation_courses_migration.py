"""Allow Orientation courses to exist without a school, programme, or catalogue course."""
import asyncio
from pathlib import Path

import asyncpg

from app.core.config import settings


async def apply_migration() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "app/modules/lms/sql/orientation_courses.sql"
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        await connection.execute(sql_path.read_text(encoding="utf-8"))
        print("Orientation courses can be created without a catalogue pathway.")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(apply_migration())
