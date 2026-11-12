"""Bring new Meta lead form submissions into the CRM. Run every 5 minutes by the inspire-meta-leads timer."""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import AsyncSessionLocal, engine
from app.modules.crm import meta_leads


async def main() -> int:
    engine.echo = False
    try:
        result = await meta_leads.run_once(AsyncSessionLocal)
    finally:
        await engine.dispose()
    print(result)
    return 1 if result.get("error") or result.get("failed") else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
