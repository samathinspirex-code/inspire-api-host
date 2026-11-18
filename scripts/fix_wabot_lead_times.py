"""Set "Added" on mirrored WhatsApp bot leads to when the student first messaged the bot.

From 15 Nov 2026 the server's bot sync stored each new lead's time 11 hours early
(the bot's UTC time read as Sri Lanka time and moved back 5h30 instead of forward).
Those leads land on the wrong CRM day. This reads each lead's created_at from the
bot (read-only) and sets the CRM created_at to that moment in Sri Lanka clock time,
which is how the CRM stores times of leads that came from the bot.

Only leads exactly 11 hours early are changed. Correct leads, and leads merged into
an older CRM lead (which keep their own date), are left alone, so it is safe to run
again after the server sync is updated.

    python scripts/fix_wabot_lead_times.py            preview only
    python scripts/fix_wabot_lead_times.py --apply    write the corrected times
"""

import argparse
import asyncio
import sys
from datetime import timedelta, timezone
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.modules.whatsapp_bot.leads import sl_wall_clock  # noqa: E402
from app.modules.whatsapp_bot.supabase import BotDB, configured  # noqa: E402
from app.modules.whatsapp_bot.sync import parse_ts  # noqa: E402

BUG = timedelta(hours=-5, minutes=-30)  # CRM value minus bot UTC value, for the broken rows
SLACK = timedelta(minutes=1)


async def main(apply: bool) -> int:
    engine.echo = False
    if not configured():
        print("The WhatsApp bot database is not configured here.")
        return 1
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT lead_id, external_record_id, created_at, assigned_at, full_name FROM crm_leads "
            "WHERE external_record_id LIKE 'wabot:%' AND is_archived = false"
        ))).all()
        bot_ids = [row.external_record_id.removeprefix("wabot:") for row in rows]
        bot_times = {}
        async with BotDB() as bot:
            for index in range(0, len(bot_ids), 150):
                chunk = bot_ids[index:index + 150]
                for item in await bot.select("leads", {"id": f"in.({','.join(chunk)})", "select": "id,created_at"}):
                    bot_times[item["id"]] = parse_ts(item["created_at"])

        fixes = []
        for row in rows:
            bot_time = bot_times.get(row.external_record_id.removeprefix("wabot:"))
            if bot_time is None:
                continue
            utc = bot_time.astimezone(timezone.utc).replace(tzinfo=None)
            if abs((row.created_at - utc) - BUG) > SLACK:
                continue
            correct = sl_wall_clock(bot_time)
            # Assignment made in the same pass as creation carries the same wrong value.
            fix_assigned = row.assigned_at is not None and abs(row.assigned_at - row.created_at) <= SLACK
            fixes.append((row, correct, fix_assigned))

        print(f"{len(rows)} bot leads in the CRM; {len(fixes)} are 11 hours early.")
        for row, correct, fix_assigned in sorted(fixes, key=lambda item: item[1]):
            note = " (+ assigned time)" if fix_assigned else ""
            print(f"  lead {row.lead_id:>6}  {row.created_at:%d %b %H:%M} -> {correct:%d %b %H:%M}{note}  {row.full_name or ''}")
        if not apply:
            print("Preview only. Run with --apply to write these times.")
            return 0
        for row, correct, fix_assigned in fixes:
            await db.execute(text(
                "UPDATE crm_leads SET created_at = :correct"
                + (", assigned_at = :correct" if fix_assigned else "")
                + " WHERE lead_id = :id AND created_at = :old"
            ), {"correct": correct, "id": row.lead_id, "old": row.created_at})
        await db.commit()
        print(f"Updated {len(fixes)} leads.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the corrected times")
    raise SystemExit(asyncio.run(main(parser.parse_args().apply)))
