"""Run one daily billing pass and/or one outbox delivery pass. Schedule externally."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.modules.payments import jobs
from app.modules.payments.db import close_pool


async def main(args):
    try:
        if args.daily: print(await jobs.daily())
        if args.send: print(await jobs.flush())
    finally: await close_pool()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--daily',action='store_true',help='Invoice due instalments, apply configured late fees, queue reminders')
    p.add_argument('--send',action='store_true',help='Deliver queued messages only if enabled and dummy data is absent')
    args=p.parse_args()
    if not(args.daily or args.send): p.error('Choose --daily and/or --send')
    asyncio.run(main(args))
