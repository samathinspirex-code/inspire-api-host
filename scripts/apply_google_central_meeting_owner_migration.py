"""Apply the Super Admin-owned Google Meet connection migration."""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    migration_path = Path(__file__).resolve().parents[1] / "app/modules/lms/sql/google_central_meeting_owner.sql"
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        await connection.execute(migration_path.read_text(encoding="utf-8"))
        verified = await connection.fetchval(
            "SELECT to_regclass('public.lms_google_central_account_connection') IS NOT NULL"
        )
    finally:
        await connection.close()
    if not verified:
        raise RuntimeError("Central Google account table was not created")
    print("Central Google meeting-owner migration applied successfully.")


if __name__ == "__main__":
    asyncio.run(apply_migration())
