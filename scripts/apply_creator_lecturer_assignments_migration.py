"""Backfill lecturer ownership for Course pages and classes they created."""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    sql_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "modules"
        / "lms"
        / "sql"
        / "creator_lecturer_assignments.sql"
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
        course_count = await connection.fetchval("SELECT count(*) FROM lms_course_lecturers")
        class_count = await connection.fetchval("SELECT count(*) FROM lms_class_lecturers")
    finally:
        await connection.close()
    print(
        "Creator lecturer assignments applied successfully: "
        f"{course_count} Course assignments, {class_count} class assignments"
    )


if __name__ == "__main__":
    asyncio.run(apply_migration())
