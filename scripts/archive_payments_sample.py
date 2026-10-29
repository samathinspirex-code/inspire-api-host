"""Archive the complete Payments schema before an explicit practice-ledger cutover."""
import argparse
import asyncio
import gzip
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.modules.payments import db, setup


async def run(path: Path, clear: bool):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'Backup already exists: {path}')
    async with db.transaction(write=clear) as connection:
        names = await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname='payments' ORDER BY tablename")
        archive = {
            'format': 'inspire-payments-table-snapshot-v1',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'schema_sql': (setup.ROOT / 'sql/001_payments.sql').read_text(encoding='utf-8'),
            'tables': {},
            'sequences': [dict(row) for row in await connection.fetch(
                "SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='payments' ORDER BY sequencename")],
        }
        for record in names:
            table = record['tablename']
            archive['tables'][table] = [dict(row) for row in await connection.fetch(f'SELECT * FROM payments."{table}"')]
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as output:
                with gzip.GzipFile(fileobj=output, mode='wb') as zipped:
                    zipped.write(db.dumps(archive).encode('utf-8'))
                output.flush()
                os.fsync(output.fileno())
            with gzip.open(path, 'rt', encoding='utf-8') as saved:
                checked = json.load(saved)
            if {name: len(rows) for name, rows in checked['tables'].items()} != {
                    name: len(rows) for name, rows in archive['tables'].items()}:
                raise RuntimeError('Backup verification failed')
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        print('Archived Payments tables:', {name: len(rows) for name, rows in checked['tables'].items()})
        print('Backup:', path)
        if clear:
            result = await setup.clear_sample_ledger(connection, 'sample-cutover')
            for table in ('learners', 'journals', 'invoices', 'payments', 'payment_links'):
                if await connection.fetchval(f'SELECT COUNT(*) FROM {table}'):
                    raise RuntimeError(f'Clear verification failed for {table}')
            print('Payments practice ledger cleared:', result['removed'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--clear', action='store_true', help='Clear the practice ledger after saving and verifying the backup')
    args = parser.parse_args()
    asyncio.run(run(args.backup, args.clear))
