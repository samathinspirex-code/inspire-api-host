"""Read-only checks for the academic architecture migration."""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def main():
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        tables = await connection.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_name = ANY($1::text[]) ORDER BY table_name
        """, ["programs", "lms_courses", "lms_classes", "lms_modules", "lms_learning_items", "lms_class_students"])
        counts = await connection.fetchrow("""
            SELECT (SELECT count(*) FROM programs) programs,
              (SELECT count(*) FROM lms_courses) lms_courses,
              (SELECT count(*) FROM lms_classes) classes,
              (SELECT count(*) FROM lms_modules) sections,
              (SELECT count(*) FROM lms_learning_items) items
        """)
        print("Required tables:", [row["table_name"] for row in tables])
        print("Live row counts:", dict(counts))
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
