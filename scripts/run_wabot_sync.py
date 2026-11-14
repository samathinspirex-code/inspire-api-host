"""Mirror WhatsApp bot leads into the CRM. Read-only on the bot.

    python scripts/run_wabot_sync.py                  one pass
    python scripts/run_wabot_sync.py --watch          keep running, a pass every 20 seconds (systemd service)
    python scripts/run_wabot_sync.py --since 2026-11-13   (re)start from that day, Sri Lanka time; no duplicates
    python scripts/run_wabot_sync.py --dry-run        list what would change, write nothing
"""

import argparse
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pprint import pprint

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.modules.whatsapp_bot.sync import run_sync  # noqa: E402

SRI_LANKA = timezone(timedelta(hours=5, minutes=30))
try:  # names in the bot can be Sinhala, Tamil or emoji; never crash on a narrow console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def day_start(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=SRI_LANKA) if "T" not in value else datetime.fromisoformat(value)


async def once(dry_run: bool, since):
    async with AsyncSessionLocal() as db:
        return await run_sync(db, dry_run=dry_run, since=since)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=20)
    parser.add_argument("--since")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    engine.echo = False
    if not settings.WABOT_SYNC_ENABLED and not args.dry_run:
        print("WABOT_SYNC_ENABLED is false; nothing to do.")
        return 0
    since = day_start(args.since) if args.since else None
    try:
        if not args.watch:
            result = await once(args.dry_run, since)
            pprint(result, sort_dicts=False)
            return 1 if result.get("errors") else 0
        while True:
            started = time.monotonic()
            try:
                result = await once(False, since)
                since = None  # only the first pass rescans from --since
                if any(result.get(k) for k in ("created", "merged", "updated")) or result.get("errors"):
                    print(datetime.now().isoformat(timespec="seconds"), result, flush=True)
            except Exception as exc:  # keep the service alive; the next pass retries
                print(datetime.now().isoformat(timespec="seconds"), "pass failed:", repr(exc), flush=True)
            await asyncio.sleep(max(1, args.interval - (time.monotonic() - started)))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
