"""Add connector report fields required by the CMS Connectors page."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import engine


async def main() -> None:
    engine.echo = False
    try:
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE connectors ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ"))
            await connection.execute(text("ALTER TABLE connectors ADD COLUMN IF NOT EXISTS last_error VARCHAR(240)"))
            await connection.execute(text("ALTER TABLE connectors ADD COLUMN IF NOT EXISTS report JSONB"))
        print("Connector reporting columns are ready.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
