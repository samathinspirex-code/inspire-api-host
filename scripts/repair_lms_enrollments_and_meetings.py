"""Repair and backfill LMS enrollments, course lecturer links, and meeting audiences."""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def apply_repair() -> None:
    sql_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "modules"
        / "lms"
        / "sql"
        / "repair_lms_enrollments_and_meetings.sql"
    )
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        async with connection.transaction():
            await connection.execute(sql_path.read_text(encoding="utf-8"))
        audience_count = await connection.fetchval("SELECT count(*) FROM lms_meeting_audience_classes")
        course_lecturer_count = await connection.fetchval("SELECT count(*) FROM lms_course_lecturers")
        course_student_count = await connection.fetchval("SELECT count(*) FROM lms_course_enrollments WHERE status = 'enrolled'")
    finally:
        await connection.close()
    print(
        "LMS Repair applied successfully:\n"
        f"  - {audience_count} meeting audience class entries\n"
        f"  - {course_lecturer_count} course lecturer entries\n"
        f"  - {course_student_count} active course student enrollments"
    )


if __name__ == "__main__":
    asyncio.run(apply_repair())
