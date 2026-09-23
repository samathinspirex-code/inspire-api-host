"""Add optional lecturer material attachments to coursework assignments."""

import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        await connection.execute(
            """ALTER TABLE lms_coursework_assignments
               ADD COLUMN IF NOT EXISTS material_asset_id BIGINT
               REFERENCES media_assets(media_asset_id) ON DELETE SET NULL"""
        )
    finally:
        await connection.close()
    print("Assignment materials migration applied successfully.")


if __name__ == "__main__":
    asyncio.run(apply_migration())
