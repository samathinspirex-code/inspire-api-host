"""New website housekeeping (run every 15 minutes by deploy/inspire-website-worker.timer):
- delete expired free-demo accounts,
- mark unpaid website orders older than 30 days as expired (never anything in the ledger),
- retry CRM updates for paid website orders.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.modules.payments.db import close_pool, transaction  # noqa: E402
from app.modules.website import checkout, crm_sync, demo  # noqa: E402


async def main() -> None:
    try:
        async with AsyncSessionLocal() as db:
            purged = await demo.purge_expired(db)
        async with transaction(write=True) as c:
            expired = await checkout.expire_old_orders(c)
        synced = await crm_sync.sync_paid_orders(50)
        print(f"demo accounts removed: {purged}; orders expired: {expired}; CRM leads updated: {synced}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
