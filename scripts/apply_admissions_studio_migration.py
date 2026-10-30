"""Create Admissions Studio tables and the CRM referral code column."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import engine


async def main() -> None:
    sql_path = REPO_ROOT / "app/modules/insights/sql/2026_10_30_admissions_studio.sql"
    raw = sql_path.read_text(encoding="utf-8")
    statements = [part.strip() for part in raw.split(";") if part.strip() and not part.strip().startswith("--")]
    engine.echo = False
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
    print(f"Applied {len(statements)} Admissions Studio statements.")


if __name__ == "__main__":
    asyncio.run(main())
