"""Normalize awarding-body names and repair public course slugs.

Runs as a read-only preview unless ``--apply`` is supplied. Slugs are generated
from the course title and programme name, with the course code/id added only
when needed to keep every public URL unique.
"""
import argparse
import asyncio
import re
import sys
import unicodedata
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()))
    return slug.replace("intelligance", "intelligence")


def canonical_awarding_body(value: str) -> str:
    normalized = value.strip().lower()
    if "athe" in normalized:
        return "ATHE"
    if "cpd" in normalized:
        return "CPD"
    if "winc" in normalized:
        return "WINC"
    if "lsbf" in normalized:
        return "LSBF"
    if "jain" in normalized:
        return "Jain University"
    return value.strip()


def unique_slug(row: asyncpg.Record, used: set[str]) -> str:
    title = row["title"].strip()
    programme = row["programme_name"].strip()
    candidates = [
        slugify(f"{title}-{programme}"),
        slugify(f"{title}-{programme}-{row['code']}"),
        slugify(f"{title}-{programme}-{row['course_id']}"),
    ]
    for candidate in candidates:
        if candidate and candidate not in used:
            used.add(candidate)
            return candidate
    raise RuntimeError(f"Could not create a unique slug for course {row['course_id']}")


def slug_needs_repair(value: str) -> bool:
    """Keep established clean URLs; replace unsafe or meaningless values."""
    return (
        not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value or "")
        or len(value) < 5
        or "intelligance" in value
    )


async def repair(apply_changes: bool) -> None:
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        rows = await connection.fetch(
            """
            SELECT c.course_id, c.legacy_program_id, c.slug, c.code, c.title,
                   c.awarding_body, p.name AS programme_name,
                   lp.slug AS legacy_slug, lp.awarding_body AS legacy_awarding_body
            FROM academic_courses c
            JOIN academic_programmes p ON p.programme_id = c.programme_id
            LEFT JOIN programs lp ON lp.program_id = c.legacy_program_id
            ORDER BY c.course_id
            """
        )
        linked_ids = {row["legacy_program_id"] for row in rows if row["legacy_program_id"] is not None}
        unlinked_slugs = await connection.fetch(
            "SELECT program_id, slug FROM programs WHERE NOT (program_id = ANY($1::int[]))",
            list(linked_ids),
        )
        used = {row["slug"].lower() for row in unlinked_slugs if row["slug"]}
        used.update(row["slug"].lower() for row in rows if row["slug"] and not slug_needs_repair(row["slug"]))
        changes = []
        for row in rows:
            new_slug = unique_slug(row, used) if slug_needs_repair(row["slug"]) else row["slug"]
            new_body = canonical_awarding_body(row["awarding_body"])
            if (
                row["slug"] != new_slug
                or row["legacy_slug"] != new_slug
                or row["awarding_body"] != new_body
                or row["legacy_awarding_body"] != new_body
            ):
                changes.append((row, new_slug, new_body))

        for row, new_slug, new_body in changes:
            print(
                f"{row['course_id']}: {row['slug']!r} -> {new_slug!r}; "
                f"{row['awarding_body']!r} -> {new_body!r}"
            )
        print(f"{len(changes)} of {len(rows)} courses require normalization.")

        if not apply_changes:
            print("Preview only. Run again with --apply to save these changes.")
            return

        async with connection.transaction():
            # Temporary values prevent unique-index collisions while canonical
            # slugs are reassigned to linked legacy and academic records.
            academic_temporary = [(f"catalogue-repair-{row['course_id']}", row["course_id"]) for row, _, _ in changes]
            legacy_temporary = [(f"catalogue-repair-{row['course_id']}", row["legacy_program_id"]) for row, _, _ in changes if row["legacy_program_id"] is not None]
            academic_final = [(new_slug, new_body, row["course_id"]) for row, new_slug, new_body in changes]
            legacy_final = [(new_slug, new_body, row["legacy_program_id"]) for row, new_slug, new_body in changes if row["legacy_program_id"] is not None]
            await connection.executemany("UPDATE academic_courses SET slug=$1 WHERE course_id=$2", academic_temporary)
            await connection.executemany("UPDATE programs SET slug=$1 WHERE program_id=$2", legacy_temporary)
            await connection.executemany(
                "UPDATE academic_courses SET slug=$1, awarding_body=$2, updated_at=now() WHERE course_id=$3",
                academic_final,
            )
            await connection.executemany(
                "UPDATE programs SET slug=$1, awarding_body=$2 WHERE program_id=$3",
                legacy_final,
            )
        print("Catalogue slugs and awarding-body names updated successfully.")
    finally:
        await connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Save the proposed changes")
    arguments = parser.parse_args()
    asyncio.run(repair(arguments.apply))
