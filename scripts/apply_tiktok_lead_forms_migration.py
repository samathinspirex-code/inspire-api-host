"""Classify legacy generic TikTok leads as TikTok Lead Forms."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import engine


async def main() -> None:
    sql_path = REPO_ROOT / "app/modules/crm/sql/2026_10_29_tiktok_lead_forms.sql"
    sql = "\n".join(line for line in sql_path.read_text(encoding="utf-8").splitlines()
                    if not line.lstrip().startswith("--"))
    engine.echo = False
    async with engine.begin() as connection:
        before = (await connection.execute(text(
            "SELECT count(*) FROM crm_leads WHERE source IN ('tiktok', 'tik_tok')"
        ))).scalar_one()
        await connection.execute(text(sql))
        after = (await connection.execute(text(
            "SELECT count(*) FROM crm_leads WHERE source IN ('tiktok', 'tik_tok')"
        ))).scalar_one()
        if after:
            raise RuntimeError(f"{after} leads still have a generic TikTok source")
        print(f"Reclassified {before} generic TikTok leads as TikTok Lead Forms.")


if __name__ == "__main__":
    asyncio.run(main())
