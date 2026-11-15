"""Create the internal school mappings for awarding bodies without a school step.

LSBF, Jain University and Inspire College courses go straight to programme
selection in the CMS, but academic courses still need a school_id. Each of these
bodies gets one archived (hidden) school that the CMS fills in automatically.
Safe to run again.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import engine

# code -> (name, position); codes must match INTERNAL_SCHOOL_CODES in CMS-UI AcademicStructure.tsx.
INTERNAL_SCHOOLS = {
    "INSPIRE": ("Inspire College", 90),
    "JAIN": ("Jain University", 91),
    "LSBF": ("LSBF", 92),
}


async def main() -> None:
    engine.echo = False
    try:
        async with engine.begin() as connection:
            for code, (name, position) in INTERNAL_SCHOOLS.items():
                exists = await connection.scalar(text(
                    "SELECT school_id FROM academic_schools WHERE upper(code) = :c"), {"c": code})
                if exists is None:
                    await connection.execute(text("""
                        INSERT INTO academic_schools (code, name, description, status, position)
                        VALUES (:c, :n, 'Internal catalogue mapping; students choose a programme directly.',
                                'archived', :p)
                    """), {"c": code, "n": name, "p": position})
                await connection.execute(text(
                    "UPDATE academic_schools SET status = 'archived' WHERE upper(code) = :c AND status <> 'archived'"),
                    {"c": code})
                if await connection.scalar(text(
                        "SELECT school_id FROM academic_schools WHERE upper(code) = :c"), {"c": code}) is None:
                    raise RuntimeError(f"The {name} internal school mapping could not be created")
        print("Internal school mappings ready: " + ", ".join(n for n, _ in INTERNAL_SCHOOLS.values()))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
