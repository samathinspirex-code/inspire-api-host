"""Move upcoming Zoom meetings back to the time the LMS has for them.

Meetings created before the time zone fix were scheduled in Zoom 5h30 early
(Zoom read the UTC clock time as Sri Lanka time). The LMS time is correct.
This compares each upcoming Zoom class with Zoom and, with --apply, updates only
Zoom's start time (join links and passcodes stay the same).

Dry run (default) lists the differences; pass --apply to fix them in Zoom.
"""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.INFO)
from sqlalchemy import text  # noqa: E402

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.modules.lms.zoom_service import (  # noqa: E402
    DEFAULT_CLASS_TIMEZONE, ZOOM_API, _access_token, _zoom_local_start,
)


async def main(apply: bool) -> None:
    engine.echo = False
    try:
        async with AsyncSessionLocal() as db:
            meetings = (await db.execute(text("""SELECT meeting_id, title, provider_meeting_id, zoom_host_connection_id,
                start_time, end_time, timezone FROM lms_online_meetings
              WHERE provider='zoom' AND status='scheduled' AND end_time > now() AND provider_meeting_id IS NOT NULL
              ORDER BY start_time"""))).mappings().all()
            tokens: dict[int, str] = {}
            wrong = fixed = 0
            async with httpx.AsyncClient(timeout=25) as client:
                for m in meetings:
                    host_id = m["zoom_host_connection_id"]
                    if host_id not in tokens:
                        host = (await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id"),
                                                 {"id": host_id})).mappings().first()
                        if not host:
                            print(f"meeting {m['meeting_id']}: host account not connected, skipped")
                            continue
                        tokens[host_id] = await _access_token(db, dict(host))
                    headers = {"Authorization": f"Bearer {tokens[host_id]}"}
                    response = await client.get(f"{ZOOM_API}/meetings/{m['provider_meeting_id']}", headers=headers)
                    if response.is_error:
                        print(f"meeting {m['meeting_id']}: Zoom lookup failed ({response.status_code}), skipped")
                        continue
                    remote = response.json()
                    zoom_start = datetime.fromisoformat(remote["start_time"].replace("Z", "+00:00")) if remote.get("start_time") else None
                    if zoom_start == m["start_time"]:
                        continue
                    wrong += 1
                    tz = m["timezone"] or DEFAULT_CLASS_TIMEZONE
                    print(f"meeting {m['meeting_id']} ({m['title'][:50]}): LMS {m['start_time']:%Y-%m-%d %H:%M} UTC, "
                          f"Zoom {zoom_start:%Y-%m-%d %H:%M} UTC -> set Zoom to {_zoom_local_start(m['start_time'], tz)} {tz}")
                    if not apply:
                        continue
                    duration = max(1, round((m["end_time"] - m["start_time"]).total_seconds() / 60))
                    patch = await client.patch(f"{ZOOM_API}/meetings/{m['provider_meeting_id']}", headers=headers,
                        json={"start_time": _zoom_local_start(m["start_time"], tz), "timezone": tz, "duration": duration})
                    if patch.is_error:
                        print(f"  failed: {patch.status_code} {patch.text[:200]}")
                    else:
                        fixed += 1
            print(f"{len(meetings)} upcoming Zoom classes checked, {wrong} at the wrong time in Zoom"
                  + (f", {fixed} fixed" if apply else ". Dry run only; re-run with --apply to fix them in Zoom."))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
