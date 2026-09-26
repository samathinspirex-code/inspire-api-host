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
                "ALTER TABLE crm_leads "
                "ADD COLUMN IF NOT EXISTS external_record_id VARCHAR(100)"
            )
        )
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ix_crm_leads_external_record_id "
                "ON crm_leads (external_record_id) "
                "WHERE external_record_id IS NOT NULL"
            )
        )
    await engine.dispose()
    print("CRM external record ID migration applied.")


if __name__ == "__main__":
    asyncio.run(main())
