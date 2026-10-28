"""Rename the Contactable CRM stage to Contacted in one transaction."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import engine


async def main() -> None:
    sql_path = REPO_ROOT / "app/modules/crm/sql/2026_10_28_contacted_stage.sql"
    sql = "\n".join(line for line in sql_path.read_text(encoding="utf-8").splitlines()
                    if not line.lstrip().startswith("--"))
    engine.echo = False
    async with engine.begin() as connection:
        before = (await connection.execute(text(
            "SELECT count(*) FROM crm_leads WHERE stage='contactable'"
        ))).scalar_one()
        for statement in (part.strip() for part in sql.split(";")):
            if statement:
                await connection.execute(text(statement))
        after = (await connection.execute(text(
            "SELECT count(*) FROM crm_leads WHERE stage='contactable'"
        ))).scalar_one()
        if after:
            raise RuntimeError(f"{after} leads still have the old stage")
        print(f"Converted {before} CRM leads from Contactable to Contacted.")


if __name__ == "__main__":
    asyncio.run(main())
