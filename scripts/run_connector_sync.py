"""Refresh every connector report that has working access. Exits 1 if any account failed."""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import AsyncSessionLocal, engine
from app.modules.connectors.service import sync_all


async def main() -> int:
    engine.echo = False
    try:
        results = await sync_all(AsyncSessionLocal)
    finally:
        await engine.dispose()
    for item in results:
        print(f"#{item['id']} {item['platform']}: {item['outcome']}")
    print(f"{len(results)} accounts checked.")
    return 1 if any(item["outcome"] != "synced" for item in results) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
