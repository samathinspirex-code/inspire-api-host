"""Apply the programme/course/template/class architecture migration."""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def apply_migration() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "app/modules/academic/sql/academic_architecture.sql"
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
        counts = await connection.fetchrow("""
            SELECT (SELECT count(*) FROM academic_programmes) programmes,
                   (SELECT count(*) FROM academic_levels) levels,
                   (SELECT count(*) FROM academic_schools) schools,
                   (SELECT count(*) FROM academic_courses) courses,
                   (SELECT count(*) FROM academic_course_templates) templates,
                   (SELECT count(*) FROM academic_course_template_versions) template_versions,
                   (SELECT count(*) FROM lms_classes WHERE academic_course_id IS NOT NULL) converted_classes,
                   (SELECT count(*) FROM lms_classes WHERE study_mode IS NOT NULL) inferred_class_modes,
                   (SELECT count(*) FROM academic_course_enrolments) course_enrolments,
                   (SELECT count(*) FROM academic_migration_review WHERE status='open') review_required
        """)
        review_rows = await connection.fetch("""
            SELECT record_type, record_id, reason, status
            FROM academic_migration_review ORDER BY record_type, record_id
        """)
    finally:
        await connection.close()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": dict(counts),
        "review_required": [dict(row) for row in review_rows],
    }
    report_path = Path(__file__).resolve().parents[1] / "academic-migration-report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("Academic architecture migration applied:", dict(counts))
    print("Migration report:", report_path)


if __name__ == "__main__":
    asyncio.run(apply_migration())
