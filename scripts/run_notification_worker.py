"""Run the durable LMS reminder and Mailjet delivery worker."""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.modules.lms.notification_service import dispatch_cycle  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("inspire-notifications")


async def run_once():
    async with AsyncSessionLocal() as db:
        summary = await dispatch_cycle(db)
        logger.info("reminders=%s announcements=%s sent=%s failed=%s", summary.reminders_created,
            summary.announcements_published, summary.emails_sent, summary.emails_failed)


async def main(once: bool):
    try:
        while True:
            try: await run_once()
            except Exception: logger.exception("Notification cycle failed")
            if once: break
            await asyncio.sleep(60)
    finally: await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()
    asyncio.run(main(args.once))
