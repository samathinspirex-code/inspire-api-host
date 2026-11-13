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
    added = covered = runs_covered = 0
    try:
        statements = [item.strip() for item in migration_path.read_text(encoding="utf-8").split(";") if item.strip()]
        async with connection.transaction():
            for statement in statements:
                await connection.execute(statement)
            for item in starters:
                row = await connection.fetchrow(
                    """
                    INSERT INTO lms_sim_templates (starter_key, title, tagline, cover_image_url, programme, level, tone, content, roles)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb)
                    ON CONFLICT (starter_key) DO NOTHING
                    RETURNING template_id
                    """,
                    item["starter_key"], item["title"], item["tagline"], item.get("cover_image_url"), item["programme"], item["level"], item["tone"],
                    json.dumps(item["content"]), json.dumps(item["roles"]),
                )
                added += 1 if row else 0
                # Give starters that already exist their banner, without replacing one set by hand.
                status = await connection.execute(
                    "UPDATE lms_sim_templates SET cover_image_url = $2 WHERE starter_key = $1 AND cover_image_url IS NULL",
                    item["starter_key"], item.get("cover_image_url"),
                )
                covered += int(status.split()[-1])
            # Class runs copy the cover when created, so fill runs made from starters before they had one.
            status = await connection.execute(
                """
                UPDATE lms_sim_runs AS r SET cover_image_url = t.cover_image_url
                FROM lms_sim_templates AS t
                WHERE r.template_id = t.template_id AND t.starter_key IS NOT NULL
                  AND r.cover_image_url IS NULL AND t.cover_image_url IS NOT NULL
                """
            )
            runs_covered = int(status.split()[-1])
    finally:
        await connection.close()
    print(f"Simulator tables are ready. Starter templates added: {added} of {len(starters)}. "
          f"Banners set on {covered} starters and {runs_covered} class runs.")


if __name__ == "__main__":
    asyncio.run(apply_migration())
