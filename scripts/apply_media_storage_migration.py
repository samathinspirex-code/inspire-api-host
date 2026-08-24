"""Create the shared media library and add program cover-image support."""

import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    modules = Path(__file__).resolve().parents[1] / "app" / "modules"
    root = modules / "cms" / "sql"
    sql = "\n".join(
        (root / filename).read_text(encoding="utf-8")
        for filename in ("media_assets.sql", "program_media.sql")
    )
    sql += "\n" + (modules / "lms" / "sql" / "profile_media.sql").read_text(encoding="utf-8")
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
            await connection.execute(sql)
    finally:
        await connection.close()
    print("Media storage migration applied successfully.")


if __name__ == "__main__":
    asyncio.run(apply_migration())
