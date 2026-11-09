"""Create the simulator tables and load the 14 starter templates. Safe to run more than once."""
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    migration_path = ROOT / "app/modules/lms/sql/simulators.sql"
    starters = json.loads((ROOT / "app/modules/lms/data/simulator_starters.json").read_text(encoding="utf-8"))
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    added = 0
    try:
        statements = [item.strip() for item in migration_path.read_text(encoding="utf-8").split(";") if item.strip()]
        async with connection.transaction():
            for statement in statements:
                await connection.execute(statement)
            for item in starters:
                row = await connection.fetchrow(
                    """
                    INSERT INTO lms_sim_templates (starter_key, title, tagline, programme, level, tone, content, roles)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
                    ON CONFLICT (starter_key) DO NOTHING
                    RETURNING template_id
                    """,
                    item["starter_key"], item["title"], item["tagline"], item["programme"], item["level"], item["tone"],
                    json.dumps(item["content"]), json.dumps(item["roles"]),
                )
                added += 1 if row else 0
    finally:
        await connection.close()
    print(f"Simulator tables are ready. Starter templates added: {added} of {len(starters)}.")


if __name__ == "__main__":
    asyncio.run(apply_migration())
