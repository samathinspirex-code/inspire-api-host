"""Verify the configured Payments database; optional dummy workflow always rolls back."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.payments import billing, closing, core, ledger, operations, payhere, reports
from app.modules.payments.db import MUTATION_LOCK, codecs, connect


async def verify(exercise=False):
    c = await connect()
    try:
        await codecs(c)
        counts = {name: await c.fetchval(f'SELECT COUNT(*) FROM {name}')
                  for name in ['learners', 'journals', 'invoices', 'payments', 'payment_links']}
        print('Saved records:', counts, flush=True)
        demo = await c.fetchval('SELECT demo_data FROM module_state WHERE id=true')
        print('Dummy ledger:', bool(demo), flush=True)
        for kind in ['trial-balance', 'balance-sheet', 'cash-flow']:
            result = await reports.report(c, kind, {})
            if not result['check']['ok']:
                raise RuntimeError(kind + ' did not reconcile')
            print(kind + ': PASS', flush=True)
        for kind in ['profit-loss', 'ar-ageing', 'ap-ageing', 'deferred-revenue', 'general-ledger']:
            await reports.report(c, kind, {})
            print(kind + ': PASS', flush=True)
        await reports.dashboard(c)
        print('Dashboard: PASS', flush=True)
        if not exercise:
            return
        if not demo:
            raise RuntimeError('Write exercises require the explicitly labelled dummy ledger')
        transaction = c.transaction()
        await transaction.start()
        try:
            await c.execute('SELECT pg_advisory_xact_lock($1)', MUTATION_LOCK)
            learner = await billing.create_learner(c, dict(first_name='Verification', last_name='Temporary',
                email='verification@example.com', phone='+94770000000'), 'verification')
            await billing.enrol(c, dict(learner_id=learner['id'], programme_code='HND-BUS', start_date=core.today(),
                total_fee=10000, instalments=2, registration_fee=0), 'verification')
            link = await billing.create_link(c, dict(learner_id=learner['id'], kind='instalment', channel='email'), 'verification')
            receipt = await payhere.simulate(c, link['id'], 'verification')
            if not receipt['posted']:
                raise RuntimeError('Sample receipt did not post')
            print('Learner, enrolment, invoice, link and simulated receipt: PASS', flush=True)
            supplier = await operations.create_supplier(c, dict(name='Verification temporary supplier'), 'verification')
            bill = await operations.create_bill(c, dict(supplier_id=supplier['id'], bill_date=core.today(),
                expense_account='6090', description='Temporary verification bill', amount=100), 'verification')
            await operations.pay_bill(c, bill['id'], {}, 'verification')
            print('Supplier, bill and bill payment: PASS', flush=True)
            j = await ledger.post(c, dict(entry_date=core.today(), memo='Temporary verification journal', source='manual',
                lines=[dict(account_code='1020', debit=20), dict(account_code='3010', credit=20)]), 'verification')
            await ledger.reverse(c, j['id'], core.today(), 'Verification rollback exercise', 'verification')
            print('Journal and reversal: PASS', flush=True)
            for kind in ['revenue', 'depreciation', 'ecl']:
                await closing.run(c, core.today(), kind, 'verification', True)
            for kind in ['trial-balance', 'balance-sheet', 'cash-flow']:
                if not (await reports.report(c, kind, {}))['check']['ok']:
                    raise RuntimeError(kind + ' failed after workflow')
            await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
            print('Close previews, reconciliation and database constraints: PASS', flush=True)
        finally:
            await transaction.rollback()
        after = {name: await c.fetchval(f'SELECT COUNT(*) FROM {name}') for name in counts}
        if counts != after:
            raise RuntimeError('Verification record counts changed unexpectedly')
        print('All temporary workflow records rolled back; saved sample ledger preserved.', flush=True)
    finally:
        await c.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exercise', action='store_true', help='Exercise writes inside a rolled-back transaction; dummy data only')
    asyncio.run(verify(parser.parse_args().exercise))
