"""Add managed media references to LMS announcements."""
import asyncio
import sys
from pathlib import Path
import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def apply_migration():
    path = Path(__file__).resolve().parents[1] / "app/modules/lms/sql/announcement_attachments.sql"
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        async with connection.transaction():
            await connection.execute(path.read_text(encoding="utf-8"))
    finally:
        await connection.close()
    print("Announcement attachments migration applied successfully")


if __name__ == "__main__":
    asyncio.run(apply_migration())
