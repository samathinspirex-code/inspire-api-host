"""Re-score synced live-class attendance against the scheduled class length at 75%.

Students are present when they attended at least 75% of the scheduled class time
(end_time - start_time). This recomputes percentage and present/absent from the
stored attended_seconds, so Zoom is not called again. Manual overrides keep their
status. It also sets the Zoom attendance threshold setting to 75%.

Dry run (default) prints what would change; pass --apply to write.
"""
import asyncio
import math
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402

THRESHOLD = 75

ROWS = """
SELECT r.attendance_record_id, r.status, r.source, r.attended_seconds, r.attendance_percentage,
       GREATEST(1, EXTRACT(EPOCH FROM (m.end_time - m.start_time))::int) AS class_seconds,
       m.meeting_id, m.title
FROM lms_attendance_records r
JOIN lms_attendance_sessions s ON s.attendance_session_id = r.attendance_session_id
JOIN lms_online_meetings m ON m.meeting_id = s.meeting_id
WHERE s.sync_status = 'synced'
"""


async def main(apply: bool) -> None:
    connection = await asyncpg.connect(user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None)
    try:
        rows = await connection.fetch(ROWS)
        changes = []
        flips = 0
        for row in rows:
            base = row["class_seconds"]
            seconds = row["attended_seconds"] or 0
            percentage = min(100, round(seconds * 100 / base, 2))
            status = "present" if seconds >= math.ceil(base * THRESHOLD / 100) else "absent"
            if row["source"] == "manual_override":
                status = row["status"]
            if status != row["status"]:
                flips += 1
                print(f"meeting {row['meeting_id']} ({row['title']}): record {row['attendance_record_id']} "
                      f"{row['status']} -> {status} ({seconds // 60}m of {base // 60}m)")
            if status != row["status"] or abs(percentage - float(row["attendance_percentage"])) > 0.01:
                changes.append((row["attendance_record_id"], status, percentage))
        print(f"{len(rows)} records checked, {len(changes)} to update, {flips} present/absent changes")
        if not apply:
            print("Dry run only. Re-run with --apply to write.")
            return
        async with connection.transaction():
            await connection.executemany(
                "UPDATE lms_attendance_records SET status=$2, attendance_percentage=$3 WHERE attendance_record_id=$1",
                changes,
            )
            await connection.execute(
                "UPDATE lms_attendance_sessions SET threshold_percentage=$1 WHERE sync_status='synced'", THRESHOLD)
            await connection.execute(
                "UPDATE lms_zoom_settings SET attendance_threshold_percentage=$1, updated_at=now() WHERE settings_id=1",
                THRESHOLD)
        print("Applied. Students' attendance points catch up the next time they open their Attendance page.")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
