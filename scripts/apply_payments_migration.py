"""Create isolated accounting tables; optionally preserve the reference dummy data."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.modules.payments.setup import setup


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo',action='store_true',help='Load the original sample ledger into an empty payments module')
    args=parser.parse_args()
    result=asyncio.run(setup(args.demo))
    print('Payments schema ready.' + (' Dummy data loaded (or already present); outbound messages and live checkout remain blocked.' if args.demo else ''))
