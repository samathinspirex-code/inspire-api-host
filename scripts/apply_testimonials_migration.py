"""Create and seed the homepage video testimonials. Safe to rerun."""
import asyncio
import sys
from pathlib import Path
import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings


async def main():
    sql = (Path(__file__).resolve().parents[1] / "app/modules/cms/sql/testimonials.sql").read_text(encoding="utf-8")
    connection = await asyncpg.connect(user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST != "localhost" else None)
    try:
        async with connection.transaction():
            await connection.execute(sql)
    finally:
        await connection.close()
    print("Testimonials migration applied.")

if __name__ == "__main__":
    asyncio.run(main())
