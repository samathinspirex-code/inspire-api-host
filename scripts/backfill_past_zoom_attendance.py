"""Import attendance for past Zoom classes that were rebuilt without their Zoom link.

After the database loss, some completed classes were rebuilt from Vimeo
recordings: stored as provider 'google', with no Zoom meeting ID and with times
taken from the recording. This links each one to its Zoom meeting, sets its
scheduled time, and runs the normal attendance import (same name matching,
merging of rejoins, 75% of scheduled time rule, staff exclusion).

Dry run (default) runs the import inside a transaction and rolls it back, so it
shows exactly what would be written. Pass --apply to save.

    python scripts/backfill_past_zoom_attendance.py --class market-research [--apply]
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.INFO)
from sqlalchemy import text  # noqa: E402

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.modules.lms.zoom_service import _access_token, sync_attendance  # noqa: E402

UTC = timezone.utc

def _slot(day: str, start: str, end: str) -> tuple[datetime, datetime]:
    """Scheduled slot in UTC from a date and HH:MM UTC times."""
    return (datetime.fromisoformat(f"{day}T{start}").replace(tzinfo=UTC),
            datetime.fromisoformat(f"{day}T{end}").replace(tzinfo=UTC))


def _class(meeting_id: int, zoom_id: str, day: str, start: str, end: str, slot: str, host: int = 1) -> dict:
    begin, finish = _slot(day, start, end)
    return {"meeting_id": meeting_id, "zoom_id": zoom_id, "host": host, "start": begin, "end": finish, "slot": slot}


# LMS meeting -> Zoom meeting and the class's scheduled slot. Zoom meetings were found
# by the topic in each class recording's title. Choose a group with --class NAME.
# Class students who get no absent records (test or staff accounts), per group.
SKIP_ABSENT = {"market-research": {2}}  # Vishwa
GROUPS = {
    # AI Leadership and Business Productivity Mastery (class 11), 4:00-6:00 PM Sri Lanka.
    "ai-leadership": [
        _class(68, "84866041422", "2026-11-05", "10:30", "12:30", "4:00-6:00 PM SL"),
        _class(67, "83978617351", "2026-11-12", "10:30", "12:30", "4:00-6:00 PM SL"),
    ],
    # Market Research, B_L3_M5 September (class 5), 12:00-1:00 PM Sri Lanka. Zoom has no
    # record of the classes before 19 Oct, so those stay without attendance.
    "market-research": [
        _class(9, "83397611029", "2026-10-19", "06:30", "07:30", "12:00-1:00 PM SL"),
        # 21 Oct and 4 Nov ended well before 1:00 PM, so the class time is how long they
        # really ran in Zoom (first join to last leave), as the user decided.
        _class(8, "82943843564", "2026-10-21", "06:36:05", "07:17:44", "12:06-12:47 PM SL (ended early)"),
        _class(7, "82835755419", "2026-10-23", "06:30", "07:30", "12:00-1:00 PM SL"),
        _class(6, "81997272071", "2026-10-30", "06:30", "07:30", "12:00-1:00 PM SL"),
        _class(5, "87111299997", "2026-11-02", "06:30", "07:30", "12:00-1:00 PM SL"),
        _class(4, "83966511602", "2026-11-04", "06:47:00", "07:27:22", "12:17-12:57 PM SL (ended early)"),
        _class(3, "81948662502", "2026-11-09", "06:30", "07:30", "12:00-1:00 PM SL"),
        _class(2, "81220525025", "2026-11-11", "06:30", "07:30", "12:00-1:00 PM SL"),
        _class(1, "82692884852", "2026-11-13", "06:30", "07:30", "12:00-1:00 PM SL"),
    ],
    # AI Mastery, September Intake (class 19), Sundays 1:00-3:00 PM Sri Lanka. Zoom has
    # no record of the classes before 25 Oct.
    "ai-mastery": [
        _class(217, "81432549335", "2026-10-25", "07:30", "09:30", "1:00-3:00 PM SL"),
        # 1 Nov ended an hour early, so its class time is how long it really ran.
        _class(216, "87414635002", "2026-11-01", "07:31:47", "08:32:18", "1:01-2:02 PM SL (ended early)"),
        _class(215, "83120289047", "2026-11-08", "07:30", "09:30", "1:00-3:00 PM SL"),
    ],
    # AI for HR, October Intake (class 20), Sundays 9:00 AM-12:00 PM Sri Lanka. On 1 Nov
    # the class ran in a second Zoom meeting (the first one only had a 1-minute join).
    "ai-for-hr": [
        _class(248, "86191457996", "2026-11-01", "03:30", "06:30", "9:00 AM-12:00 PM SL"),
        _class(247, "83531271368", "2026-11-08", "03:30", "06:30", "9:00 AM-12:00 PM SL"),
    ],
}


async def backfill(item: dict, apply: bool, skip: set[int]) -> None:
    meeting_id = item["meeting_id"]
    async with AsyncSessionLocal() as db:
        meeting = (await db.execute(text("""SELECT meeting_id, title, status, provider, lecturer_user_id,
            (SELECT count(*) FROM lms_attendance_sessions s WHERE s.meeting_id=m.meeting_id) AS sessions
            FROM lms_online_meetings m WHERE meeting_id=:id"""), {"id": meeting_id})).mappings().first()
        if not meeting:
            print(f"meeting {meeting_id}: not found, skipped")
            return
        if meeting["status"] != "completed" or meeting["sessions"]:
            print(f"meeting {meeting_id}: not a completed class without attendance, skipped")
            return
        # Get (and if needed refresh) the Zoom token outside the preview transaction,
        # so a rotated refresh token is always saved.
        host = (await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id"),
                                 {"id": item["host"]})).mappings().first()
        await _access_token(db, dict(host))

    async with AsyncSessionLocal() as db:
        if not apply:
            db.commit = db.flush  # preview: keep everything in one transaction, rolled back below
        await db.execute(text("""UPDATE lms_online_meetings SET provider='zoom', provider_meeting_id=:zoom,
            provider_meeting_uuid=NULL, zoom_host_connection_id=:host, timezone='Asia/Colombo',
            start_time=:start, end_time=:end, updated_at=now() WHERE meeting_id=:id"""),
            {"zoom": item["zoom_id"], "host": item["host"], "start": item["start"], "end": item["end"], "id": meeting_id})
        try:
            await sync_attendance(db, meeting_id, meeting["lecturer_user_id"])
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            print(f"meeting {meeting_id}: import failed, nothing changed: {exc}")
            return
        # Students were re-added to the class after the rebuild (18 Nov), so the import
        # skips anyone with no Zoom match as "enrolled after the class". For these
        # rebuilt classes, every current class student without a match was absent.
        await db.execute(text("""INSERT INTO lms_attendance_records(attendance_session_id, student_user_id, status,
              attended_seconds, attendance_percentage, source)
            SELECT s.attendance_session_id, cs.student_user_id, 'absent', 0, 0, 'zoom'
            FROM lms_attendance_sessions s
            JOIN lms_online_meetings m ON m.meeting_id = s.meeting_id
            JOIN lms_class_students cs ON cs.class_id = m.class_id
            JOIN users u ON u.user_id = cs.student_user_id AND u.is_active
            WHERE s.meeting_id = :id AND cs.student_user_id <> m.lecturer_user_id
              AND cs.student_user_id NOT IN (SELECT cl.lecturer_user_id FROM lms_class_lecturers cl WHERE cl.class_id = m.class_id)
              AND NOT (cs.student_user_id = ANY(:skip))
            ON CONFLICT (attendance_session_id, student_user_id) DO NOTHING"""), {"id": meeting_id, "skip": list(skip)})
        rows = (await db.execute(text("""SELECT u.full_name, r.status, r.attended_seconds, r.attendance_percentage,
              r.google_participant_name FROM lms_attendance_records r
            JOIN lms_attendance_sessions s ON s.attendance_session_id=r.attendance_session_id
            JOIN users u ON u.user_id=r.student_user_id WHERE s.meeting_id=:id
            ORDER BY r.status DESC, r.attended_seconds DESC"""), {"id": meeting_id})).mappings().all()
        unmatched = await db.scalar(text("SELECT unmatched_participants FROM lms_attendance_sessions WHERE meeting_id=:id"),
                                    {"id": meeting_id}) or []
        present = sum(row["status"] == "present" for row in rows)
        print(f"\n{item['start']:%d %b} — meeting {meeting_id} ({meeting['title'][:60]}), Zoom {item['zoom_id']}, {item['slot']}")
        print(f"  {present} present, {len(rows) - present} absent of {len(rows)} students")
        for row in rows:
            minutes = round((row["attended_seconds"] or 0) / 60)
            zoom_name = f"  [Zoom: {row['google_participant_name']}]" if row["google_participant_name"] else ""
            print(f"  {row['status']:<8}{minutes:>4} min {float(row['attendance_percentage']):>6.1f}%  {row['full_name']}{zoom_name}")
        if unmatched:
            print("  Unmatched Zoom names: " + ", ".join(
                f"{u.get('display_name')} ({round((u.get('attended_seconds') or 0) / 60)} min)" for u in unmatched))
        if apply:
            await db.commit()
        else:
            await db.rollback()


async def main(group: str, apply: bool) -> None:
    engine.echo = False
    try:
        for item in GROUPS[group]:
            await backfill(item, apply, SKIP_ABSENT.get(group, set()))
        print("\nSaved." if apply else "\nDry run only, nothing saved. Re-run with --apply to save.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    args = sys.argv[1:]
    name = args[args.index("--class") + 1] if "--class" in args else ""
    if name not in GROUPS:
        sys.exit("Choose a class group: --class " + " | ".join(GROUPS))
    asyncio.run(main(name, "--apply" in args))
