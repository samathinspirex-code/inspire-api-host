"""Create the Meta lead intake tables. Safe to run more than once. Stops if Meta lead IDs are already duplicated."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import engine

SQL = REPO_ROOT / "app/modules/crm/sql/2026_11_10_meta_lead_intake.sql"


def statements() -> list[str]:
    source = "\n".join(line.split("--")[0] for line in SQL.read_text(encoding="utf-8").splitlines())
    return [item.strip() for item in source.split(";") if item.strip()]


async def main() -> int:
    engine.echo = False
    try:
        async with engine.begin() as connection:
            duplicates = await connection.scalar(text(
                "SELECT count(*) FROM (SELECT social_lead_id FROM crm_leads WHERE social_lead_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1) d"))
            if duplicates:
                print(f"{duplicates} Meta lead IDs appear on more than one lead. Resolve them first; nothing was changed.")
                return 1
            for statement in statements():
                await connection.execute(text(statement))
        print("Meta lead intake tables are ready.")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
