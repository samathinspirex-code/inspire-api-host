"""Create the internal school mapping for Inspire College courses.

Academic courses require a school_id even when the school step is hidden.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import engine


async def main() -> None:
    engine.echo = False
    try:
        async with engine.begin() as connection:
            await connection.execute(text("""
                INSERT INTO academic_schools (code, name, description, status, position)
                VALUES ('INSPIRE', 'Inspire College',
                        'Internal catalogue mapping; students choose a programme directly.',
                        'archived', 90)
                ON CONFLICT DO NOTHING
            """))
            await connection.execute(text("""
                UPDATE academic_schools SET status = 'archived'
                WHERE upper(code) = 'INSPIRE' AND status <> 'archived'
            """))
            school_id = await connection.scalar(text("""
                SELECT school_id FROM academic_schools WHERE upper(code) = 'INSPIRE'
            """))
            if school_id is None:
                raise RuntimeError("The Inspire College internal school mapping could not be created")
        print("Inspire College awarding body mapping is ready.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
