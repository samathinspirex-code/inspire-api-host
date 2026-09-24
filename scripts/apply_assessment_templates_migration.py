"""Create the shared assignment and practice-test question banks."""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    migration_path = Path(__file__).resolve().parents[1] / "app/modules/lms/sql/assessment_templates.sql"
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        statements = [item.strip() for item in migration_path.read_text(encoding="utf-8").split(";") if item.strip()]
        for statement in statements:
            await connection.execute(statement)
        count = await connection.fetchval("SELECT count(*) FROM lms_assessment_templates")
    finally:
        await connection.close()
    print(f"Shared question banks are ready ({count} templates).")


if __name__ == "__main__":
    asyncio.run(apply_migration())
