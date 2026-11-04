"""Send queued Meta Conversions API events.

  run_meta_capi.py                  Normal run. Does nothing unless sending is switched on.
  run_meta_capi.py --test CODE      Send one made-up event to Meta's Test Events tab.
  run_meta_capi.py --withdraw ID    Remove a lead's ad consent and cancel anything unsent.
"""

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import AsyncSessionLocal, engine
from app.modules.connectors import capi


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test")
    parser.add_argument("--withdraw", type=int)
    args = parser.parse_args()
    engine.echo = False
    try:
        if args.test:
            delivered, message = await capi.send_test_event(args.test)
            print("Test event accepted. Check Test Events in Events Manager." if delivered else message)
            return 0 if delivered else 1
        if args.withdraw:
            async with AsyncSessionLocal() as db:
                await capi.withdraw(db, args.withdraw)
            print(f"Consent removed for lead {args.withdraw}.")
            return 0
        result = await capi.run_once(AsyncSessionLocal)
    finally:
        await engine.dispose()
    print(result)
    return 1 if result.get("retry") else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
