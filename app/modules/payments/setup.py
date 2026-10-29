"""Explicit migration and optional one-time snapshot import. Never run on startup."""
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from . import core, ledger, reports
from .db import MUTATION_LOCK, audit, codecs, connect

ROOT=Path(__file__).parent


class _ImportBatch:
    """Pipeline consecutive inserts; flush before reads or dependent updates."""
    def __init__(self, connection):
        self.connection = connection
        self.groups = []

    def transaction(self):
        return self.connection.transaction()

    async def flush(self):
        for sql, values in self.groups:
            await self.connection.executemany(sql, values)
        self.groups.clear()

    async def execute(self, sql, *values):
        if sql.lstrip().upper().startswith('INSERT INTO') or sql.startswith('UPDATE instalments SET invoice_id='):
            if not self.groups or self.groups[-1][0] != sql:
                self.groups.append((sql, []))
            self.groups[-1][1].append(values)
            return
        await self.flush()
        return await self.connection.execute(sql, *values)

    async def fetch(self, sql, *values):
        await self.flush()
        return await self.connection.fetch(sql, *values)

    async def fetchrow(self, sql, *values):
        await self.flush()
        return await self.connection.fetchrow(sql, *values)

    async def fetchval(self, sql, *values):
        await self.flush()
        return await self.connection.fetchval(sql, *values)


async def migrate(c):
    async with c.transaction():
        await c.execute('SELECT pg_advisory_xact_lock($1)',MUTATION_LOCK)
        await c.execute((ROOT/'sql/001_payments.sql').read_text(encoding='utf-8'))
        data=json.loads((ROOT/'assets/defaults.json').read_text(encoding='utf-8'))
        for a in data['accounts']:
            await c.execute('INSERT INTO accounts(code,name,type,grp,is_system) VALUES($1,$2,$3,$4,$5) ON CONFLICT(code) DO NOTHING',a['code'],a['name'],a['type'],a['grp'],a.get('is_system',False))
        for p in data['programmes']:
            # Programme prices remain the reference placeholders until reviewed.
            await c.execute('INSERT INTO programmes(code,name,revenue_account,teaching_months,default_fee,default_instalments) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(code) DO NOTHING',
                p['code'],p['name'],p['revenue_account'],p['teaching_months'],core.money(p['default_fee']) if p['default_fee'] is not None else None,p['default_instalments'])


def uid(kind,name):
    return str(uuid5(NAMESPACE_URL,'inspire-payments-demo/'+kind+'/'+str(name)))


async def seed_demo(c):
    c = _ImportBatch(c)
    d=json.loads((ROOT/'assets/demo-snapshot.json').read_text(encoding='utf-8'))
    async with c.transaction():
        await c.execute('SELECT pg_advisory_xact_lock($1)',MUTATION_LOCK)
        if await c.fetchval("SELECT 1 FROM schema_migrations WHERE name='sample_ledger_cleared'"):
            return False
        if await c.fetchval('SELECT demo_data FROM module_state WHERE id=true'):
            return False
        for table in ['journals','learners','enrolments','instalments','invoices','payments','payment_links','suppliers','bills','bank_statement_lines','fixed_assets','message_outbox','gateway_events','fiscal_periods']:
            if await c.fetchval(f'SELECT EXISTS(SELECT 1 FROM {table})'):
                raise RuntimeError('Dummy data can only be loaded into an empty payments module')
        for l in d['learners']:
            await c.execute('INSERT INTO learners(id,learner_no,first_name,last_name,email,phone) VALUES($1,$2,$3,$4,$5,$6)',uid('learner',l[0]),*l)
        for e in d['enrolments']:
            await c.execute('INSERT INTO enrolments(id,learner_id,programme_code,start_date,total_fee,instalments,status,withdrawn_on,counsellor) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)',
                uid('enrol',e[0]),uid('learner',e[1]),e[2],e[3],core.money(e[4]),e[5],e[6],e[7],e[8])
        for i in d['instalments']:
            await c.execute('INSERT INTO instalments(id,enrolment_id,seq,due_date,amount,status) VALUES($1,$2,$3,$4,$5,$6)',uid('inst',i[0]),uid('enrol',i[1]),i[2],i[3],core.money(i[4]),i[5])
        journal_ids={}; invoice_journals={}; bill_journals={}; closes={}; journal_headers=[]; journal_lines=[]
        for j in d['journals']:
            lines=[dict(account_code=l[0],debit=core.money(l[1]),credit=core.money(l[2]),learner_id=uid('learner',l[3]) if l[3] else None,enrolment_id=uid('enrol',l[4]) if l[4] else None) for l in j[5]]
            core.validate_journal(dict(entry_date=j[1],memo=j[2],lines=lines),d['accounts'])
            bill_ref=re.search(r'BILL-\d+',j[2]) if j[3]=='bills' else None
            jid=int(j[0].split('-')[1])
            journal_headers.append((jid,j[0],j[1],j[2],j[3],j[4],'demo-import','demo:'+j[0],bill_ref.group() if bill_ref else None))
            journal_ids[j[0]]=jid
            for l in lines:
                journal_lines.append((jid,l['account_code'],l['debit'],l['credit'],l['learner_id'],l['enrolment_id']))
            if j[3]=='billing' and j[2].startswith('INV-'): invoice_journals[j[2].split(' ')[0]]=jid
            if j[3]=='bills' and j[2].startswith('BILL-'): bill_journals[j[2].split(' ')[0]]=jid
            if j[3]=='close': closes[(j[1][:7], 'revenue' if j[2].startswith('Tuition') else 'depreciation' if j[2].startswith('Depreciation') else 'ecl')]=jid
        for values in journal_headers:
            await c.execute('INSERT INTO journals(id,number,entry_date,memo,source,cash_flow_category,created_by,idempotency_key,source_ref) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)',*values)
        for values in journal_lines:
            await c.execute('INSERT INTO journal_lines(journal_id,account_code,debit,credit,learner_id,enrolment_id) VALUES($1,$2,$3,$4,$5,$6)',*values)
        for i in d['invoices']:
            await c.execute('''INSERT INTO invoices(id,number,learner_id,enrolment_id,instalment_id,kind,description,issue_date,due_date,amount,amount_paid,status,journal_id)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)''',uid('invoice',i[0]),i[0],uid('learner',i[1]),uid('enrol',i[2]) if i[2] else None,
                uid('inst',i[3]) if i[3] else None,i[4],i[5],i[6],i[7],core.money(i[8]),core.money(i[9]),i[10],invoice_journals[i[0]])
        for i in d['instalments']:
            if i[6]: await c.execute('UPDATE instalments SET invoice_id=$1 WHERE id=$2',uid('invoice',i[6]),uid('inst',i[0]))
        await c.execute("UPDATE invoices i SET cancelled_on=e.withdrawn_on FROM enrolments e WHERE i.enrolment_id=e.id AND i.status='cancelled'")
        links={l[0]:l for l in d['links']}
        for l in d['links']:
            await c.execute('''INSERT INTO payment_links(id,code,token,invoice_id,learner_id,amount,purpose,channel,status,due_date,expires_at,created_by,created_at,paid_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)''',uid('link',l[0]),l[0],secrets.token_urlsafe(24),uid('invoice',l[1]),uid('learner',l[2]),core.money(l[3]),l[4],l[5],l[6],l[7],
                datetime.now(timezone.utc)+timedelta(days=30),'demo-import',datetime.fromisoformat(l[8]).replace(tzinfo=timezone.utc),datetime.fromisoformat(l[8]).replace(tzinfo=timezone.utc) if l[6]=='paid' else None)
        for j in d['journals']:
            if j[3] not in {'receipt','payhere'}: continue
            tagged=next((l for l in j[5] if l[3]),None)
            if not tagged: raise RuntimeError('Unidentified demo receipt')
            ref=re.search(r'INV-\d+',j[2]); link=re.search(r'PL-\d+',j[2])
            inv=ref.group() if ref else links[link.group()][1] if link and link.group() in links else None
            method='payhere' if j[3]=='payhere' else 'cash' if any(l[0]=='1010' and l[1]>0 for l in j[5]) else 'bank_transfer'
            amount=sum((core.money(l[1]) for l in j[5] if l[0] in {'1010','1020','1030'}),core.ZERO)
            await c.execute('INSERT INTO payments(invoice_id,learner_id,amount,method,gateway_payment_id,received_date,journal_id) VALUES($1,$2,$3,$4,$5,$6,$7)',
                uid('invoice',inv) if inv else None,uid('learner',tagged[3]),amount,method,'demo:'+j[0] if method=='payhere' else None,j[1],journal_ids[j[0]])
        for r in d['revrec']:
            await c.execute('INSERT INTO revenue_recognition(enrolment_id,period_start,amount,journal_id) VALUES($1,$2,$3,$4)',uid('enrol',r[0]),r[1],core.money(r[2]),closes[(r[1][:7],'revenue')])
        for a in d['fixed_assets']:
            await c.execute('INSERT INTO fixed_assets(id,name,cost,salvage,acquired_on,useful_life_months,opening_depreciation) VALUES($1,$2,$3,$4,$5,$6,$7)',
                uid('asset',a['name']),a['name'],core.money(a['cost']),core.money(a['salvage']),a['acquired_on'],a['useful_life_months'],core.money(a['opening_depreciation']))
        for r in d['deprec']:
            await c.execute('INSERT INTO depreciation_runs(asset_id,period_start,amount,journal_id) VALUES($1,$2,$3,$4)',uid('asset',r[0]),r[1],core.money(r[2]),closes[(r[1][:7],'depreciation')])
        for s in d['suppliers']:
            await c.execute('INSERT INTO suppliers(id,name,kind) VALUES($1,$2,$3)',uid('supplier',s['name']),s['name'],s['kind'])
        for b in d['bills']:
            await c.execute('''INSERT INTO bills(id,number,supplier_id,bill_date,due_date,expense_account,description,amount,amount_paid,status,journal_id)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)''',uid('bill',b[0]),b[0],uid('supplier',b[1]),b[2],b[3],b[4],b[5],core.money(b[6]),core.money(b[7]),b[8],bill_journals[b[0]])
        for b in d['bank_lines']:
            await c.execute('INSERT INTO bank_statement_lines(txn_date,description,amount,external_ref,status,bank_account) VALUES($1,$2,$3,$4,$5,\'1020\')',b[0],b[1],core.money(b[2]),b[3],b[4])
        for p in d['periods']:
            await c.execute('INSERT INTO fiscal_periods(period_start,status,locked_by) VALUES($1,$2,\'demo-import\')',p['period_start'],p['status'])
        for t in d['close_tasks']:
            await c.execute('INSERT INTO close_tasks(period_start,task_key,done,done_by) VALUES($1,$2,$3,\'demo-import\')',t['period_start'],t['task_key'],t['done'])
        for seq,maximum in [('learner_number_seq',max(int(l[0][1:]) for l in d['learners'])),('invoice_number_seq',max(int(i[0][4:]) for i in d['invoices'])),
                            ('journal_number_seq',max(int(j[0][3:]) for j in d['journals'])),('journals_id_seq',max(journal_ids.values())),('payment_link_seq',max(int(l[0][3:]) for l in d['links'])),('bill_number_seq',max(int(b[0][5:]) for b in d['bills']))]:
            await c.fetchval('SELECT setval($1::regclass,$2,true)',seq,maximum)
        await c.execute('UPDATE module_state SET demo_data=true WHERE id=true')
        await audit(c,'setup','seed_demo','payments',detail=dict(as_of=d['as_of'],learners=len(d['learners']),journals=len(d['journals'])))
        for kind in ['trial-balance','balance-sheet','cash-flow']:
            report=await reports.report(c,kind,{'as_of':d['as_of'],'to':d['as_of'],'from':'2026-04-01'})
            if not report['check']['ok']: raise RuntimeError(kind+' failed reconciliation; dummy import rolled back')
    return True


async def setup(with_demo=False):
    c=await connect()
    try:
        await codecs(c)
        await migrate(c)
        if with_demo: return await seed_demo(c)
    finally: await c.close()


async def clear_sample_ledger(c, actor):
    """One-time, explicit cutover from the practice ledger to empty books."""
    from app.core.errors import ConflictError

    if not await c.fetchval('SELECT demo_data FROM module_state WHERE id=true FOR UPDATE'):
        raise ConflictError('Sample data has already been cleared')
    counts={name:await c.fetchval(f'SELECT COUNT(*) FROM {name}') for name in
            ('learners','journals','invoices','payments','payment_links','bills')}
    # All financial children are named explicitly; no CASCADE can reach CMS tables.
    await c.execute('''TRUNCATE TABLE
        audit_log, settings, fiscal_periods, journal_lines, journals, learners,
        enrolments, instalments, invoices, payment_links, gateway_events, payments,
        revenue_recognition, fixed_assets, depreciation_runs, close_tasks, suppliers,
        bills, bank_statement_lines, message_outbox, checkout_orders, request_keys
        RESTART IDENTITY''')
    for sequence in ('learner_number_seq','invoice_number_seq','journal_number_seq',
                     'payment_link_seq','bill_number_seq'):
        await c.execute(f'ALTER SEQUENCE {sequence} RESTART')
    await c.execute('UPDATE module_state SET demo_data=false WHERE id=true')
    await c.execute("INSERT INTO schema_migrations(name) VALUES('sample_ledger_cleared') ON CONFLICT DO NOTHING")
    await audit(c,actor,'clear_sample_ledger','payments',detail=dict(removed=counts))
    return dict(ok=True,removed=counts,kept=['accounts','programmes'])
