"""Create the Meta Conversions API tables. Safe to run more than once."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import engine

SQL = REPO_ROOT / "app/modules/connectors/sql/2026_11_04_meta_capi.sql"


def statements() -> list[str]:
    source = "\n".join(line.split("--")[0] for line in SQL.read_text(encoding="utf-8").splitlines())
    return [item.strip() for item in source.split(";") if item.strip()]


async def main() -> None:
    engine.echo = False
    try:
        async with engine.begin() as connection:
            for statement in statements():
                await connection.execute(text(statement))
        print("Meta Conversions API tables are ready.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
