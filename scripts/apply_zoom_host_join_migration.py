"""Add the columns that record which Zoom occurrence the host account joined."""
import asyncio
import sys
from pathlib import Path
import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402

SQL = """
ALTER TABLE lms_online_meetings ADD COLUMN IF NOT EXISTS host_joined_uuid VARCHAR(255);
ALTER TABLE lms_online_meetings ADD COLUMN IF NOT EXISTS host_joined_at TIMESTAMPTZ;
"""

async def apply_migration():
    connection = await asyncpg.connect(user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None)
    try:
        async with connection.transaction():
            await connection.execute(SQL)
    finally:
        await connection.close()
    print("Zoom host-join migration applied successfully")

if __name__ == "__main__":
    asyncio.run(apply_migration())
