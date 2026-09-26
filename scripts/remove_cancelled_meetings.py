"""Remove meetings already marked cancelled from the deployed LMS database."""
import asyncio
import argparse
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings


async def main(apply: bool = False) -> None:
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST != "localhost" else None,
    )
    try:
        targets = await connection.fetch(
            """SELECT meeting_id, title, start_time, provider
               FROM lms_online_meetings
               WHERE status='cancelled'
               ORDER BY meeting_id"""
        )
        if not targets:
            print("No cancelled meetings found.")
            return
        print(f"Found {len(targets)} cancelled meeting(s): " + ", ".join(
            f'{row["meeting_id"]} ({str(row["title"]).encode("ascii", "backslashreplace").decode("ascii")})'
            for row in targets
        ))
        if not apply:
            print("Preview only; no records removed.")
            return
        async with connection.transaction():
            removed = await connection.fetch(
                """DELETE FROM lms_online_meetings
                   WHERE status='cancelled'
                   RETURNING meeting_id, title"""
            )
        print(f"Removed {len(removed)} cancelled meeting(s): " + ", ".join(str(row["meeting_id"]) for row in removed))
    finally:
        await connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Delete the listed cancelled meetings")
    asyncio.run(main(parser.parse_args().apply))
