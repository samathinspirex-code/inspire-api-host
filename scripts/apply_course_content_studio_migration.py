"""Create the Course Content Studio tables in the configured PostgreSQL database."""

import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "app" / "modules" / "lms" / "sql" / "course_content_studio.sql"
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
        discussion_table_exists = await connection.fetchval(
            "SELECT to_regclass('public.lms_course_discussions') IS NOT NULL"
        )
        cover_image_column_exists = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'lms_courses' "
            "AND column_name = 'cover_image_url')"
        )
    finally:
        await connection.close()
    print("Course Content Studio migration applied successfully.")
    print(f"Course discussions table verified: {discussion_table_exists}")
    print(f"Course cover image column verified: {cover_image_column_exists}")


if __name__ == "__main__":
    asyncio.run(apply_migration())
