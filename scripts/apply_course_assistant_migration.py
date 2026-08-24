"""Create the course assistant settings and approved knowledge-source tables."""

import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "app" / "modules" / "lms" / "sql" / "course_assistant.sql"
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST != "localhost" else None,
    )
    try:
        async with connection.transaction():
            await connection.execute(sql_path.read_text(encoding="utf-8"))
        chunks_table_exists = await connection.fetchval(
            "SELECT to_regclass('public.lms_course_knowledge_chunks') IS NOT NULL"
        )
        source_columns = {
            row["column_name"] for row in await connection.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'lms_course_knowledge_sources' "
                "AND column_name = ANY($1::text[])",
                ["sync_key", "ingestion_status", "indexed_at"],
            )
        }
        question_tables = {
            row["table_name"] for row in await connection.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY($1::text[])",
                ["lms_lecture_questions", "lms_lecture_quiz_attempts", "lms_lecture_quiz_attempt_questions"],
            )
        }
    finally:
        await connection.close()
    print("Course assistant migration applied successfully.")
    print(f"Knowledge chunks table verified: {chunks_table_exists}")
    print(f"Source ingestion columns verified: {len(source_columns) == 3}")
    print(f"Lecture question bank tables verified: {len(question_tables) == 3}")


if __name__ == "__main__":
    asyncio.run(apply_migration())
