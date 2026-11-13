"""Copy new pending WhatsApp bot handovers (and new messages of imported chats) into the CRM.
Read-only on the bot. `--dry-run` only lists what would be imported.
Run every 5 minutes by the inspire-wabot-sync timer (deploy/inspire-wabot-sync.*.example)."""

import asyncio
import sys
from pprint import pprint
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.modules.whatsapp_bot.sync import run_sync


async def main() -> int:
    engine.echo = False
    dry_run = "--dry-run" in sys.argv
    if not settings.WABOT_SYNC_ENABLED and not dry_run:
        print("WABOT_SYNC_ENABLED is false; nothing to do.")
        return 0
    try:
        async with AsyncSessionLocal() as db:
            result = await run_sync(db, dry_run=dry_run)
    finally:
        await engine.dispose()
    pprint(result, sort_dicts=False)
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
