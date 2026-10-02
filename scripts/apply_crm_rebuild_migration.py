"""Apply the additive CRM rebuild schema before deploying the new API."""
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text
from app.core.database import engine


async def main():
    sql = (Path(__file__).resolve().parents[1] / "app/modules/crm/sql/crm_rebuild.sql").read_text()
    sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    async with engine.begin() as connection:
        for statement in (part.strip() for part in sql.split(";")):
            if statement:
                await connection.execute(text(statement))


if __name__ == "__main__":
    asyncio.run(main())
