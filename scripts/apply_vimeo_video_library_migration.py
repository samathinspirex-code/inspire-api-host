"""Apply the additive Vimeo course-video-library columns to PostgreSQL."""

import asyncio
from pathlib import Path
import sys

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "app" / "modules" / "lms" / "sql" / "vimeo_video_library.sql"
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
        course_column = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'lms_courses' AND column_name = 'vimeo_folder_uri')"
        )
        module_column = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'lms_modules' AND column_name = 'vimeo_folder_uri')"
        )
        thumbnail_column = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'lms_learning_items' AND column_name = 'thumbnail_url')"
        )
    finally:
        await connection.close()
    print(
        "Vimeo video-library migration applied. "
        f"Course column: {course_column}; module column: {module_column}; thumbnail column: {thumbnail_column}"
    )


if __name__ == "__main__":
    asyncio.run(apply_migration())
