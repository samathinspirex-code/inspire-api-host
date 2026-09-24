"""Add LMS calendar events for a class, a programme, or the whole university."""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    migration_path = Path(__file__).resolve().parents[1] / "app/modules/lms/sql/calendar_events.sql"
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        statements = [item.strip() for item in migration_path.read_text(encoding="utf-8").split(";") if item.strip()]
        for statement in statements:
            await connection.execute(statement)
    finally:
        await connection.close()
    print("Calendar events can be added for a class, a programme, or the whole university.")


if __name__ == "__main__":
    asyncio.run(apply_migration())
