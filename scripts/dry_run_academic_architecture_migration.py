"""Execute the academic migration inside a rollback-only transaction."""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def main():
    sql_path = Path(__file__).resolve().parents[1] / "app/modules/academic/sql/academic_architecture.sql"
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    transaction = connection.transaction()
    await transaction.start()
    try:
        await connection.execute(sql_path.read_text(encoding="utf-8"))
        counts = await connection.fetchrow("""
            SELECT (SELECT count(*) FROM academic_programmes) programmes,
              (SELECT count(*) FROM academic_levels) levels,
              (SELECT count(*) FROM academic_schools) schools,
              (SELECT count(*) FROM academic_courses) courses,
              (SELECT count(*) FROM academic_course_templates) templates,
              (SELECT count(*) FROM academic_course_template_versions) versions,
              (SELECT count(*) FROM academic_course_enrolments) preserved_enrolments,
              (SELECT count(*) FROM academic_migration_review) review_required
        """)
        print("Migration dry-run succeeded:", dict(counts))
    finally:
        await transaction.rollback()
        await connection.close()
        print("All dry-run changes rolled back.")


if __name__ == "__main__":
    asyncio.run(main())
