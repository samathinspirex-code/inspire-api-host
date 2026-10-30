"""Create the CMS Admin access level. Safe to rerun."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import engine


async def main() -> None:
    async with engine.begin() as connection:
        await connection.execute(text(
            "INSERT INTO access_levels (access_key, display_name, description, is_active) VALUES "
            "('CMS_ADMIN', 'CMS Admin', 'View the admissions studio sections', true) "
            "ON CONFLICT (access_key) DO UPDATE SET "
            "display_name = EXCLUDED.display_name, description = EXCLUDED.description, is_active = true"
        ))
    await engine.dispose()
    print("CMS Admin access level is ready.")


if __name__ == "__main__":
    asyncio.run(main())
