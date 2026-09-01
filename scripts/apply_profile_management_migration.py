"""Add user-editable profile fields for students and lecturers."""
import asyncio
import sys
from pathlib import Path
import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def apply_migration():
    sql = Path(__file__).resolve().parents[1] / "app" / "modules" / "lms" / "sql" / "profile_management.sql"
    connection = await asyncpg.connect(user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST != "localhost" else None)
    try:
        async with connection.transaction(): await connection.execute(sql.read_text(encoding="utf-8"))
        verified = await connection.fetchval("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='lms_student_profiles' AND column_name='bio')")
    finally: await connection.close()
    print(f"Profile management migration applied successfully. Profile fields verified: {verified}")


if __name__ == "__main__": asyncio.run(apply_migration())
