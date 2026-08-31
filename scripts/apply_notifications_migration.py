"""Create LMS announcements and durable notification delivery tables."""
import asyncio
import sys
from pathlib import Path
import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def apply_migration():
    sql = Path(__file__).resolve().parents[1] / "app" / "modules" / "lms" / "sql" / "notifications.sql"
    connection = await asyncpg.connect(user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST != "localhost" else None)
    try:
        async with connection.transaction(): await connection.execute(sql.read_text(encoding="utf-8"))
        verified = await connection.fetchval("SELECT to_regclass('public.lms_notifications') IS NOT NULL")
    finally: await connection.close()
    print(f"Notification migration applied successfully. Notifications table verified: {verified}")


if __name__ == "__main__": asyncio.run(apply_migration())
