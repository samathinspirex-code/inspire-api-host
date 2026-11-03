"""Add call outcomes, quest levels and Counsellor Arena reward tables."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import engine


async def main() -> None:
    sql_path = REPO_ROOT / "app/modules/crm/sql/2026_11_03_counsellor_arena.sql"
    raw = sql_path.read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if not line.strip().startswith("--")]
    statements = [part.strip() for part in "\n".join(lines).split(";") if part.strip()]
    engine.echo = False
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
    print(f"Applied {len(statements)} Counsellor Arena statements.")


if __name__ == "__main__":
    asyncio.run(main())
