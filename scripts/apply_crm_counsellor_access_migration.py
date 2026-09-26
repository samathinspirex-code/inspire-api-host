"""Ensure CRM-specific access levels exist. Safe to rerun."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import engine


async def main() -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO access_levels (access_key, display_name, description, is_active) VALUES "
                "('CRM', 'CRM & Admissions', 'Access CRM and admissions workspaces', true), "
                "('COUNSELLOR', 'Admissions Counsellor', 'Manage assigned admissions leads', true) "
                "ON CONFLICT (access_key) DO UPDATE SET "
                "display_name = EXCLUDED.display_name, description = EXCLUDED.description, is_active = true"
            )
        )
    await engine.dispose()
    print("CRM and counsellor access levels are ready.")


if __name__ == "__main__":
    asyncio.run(main())
