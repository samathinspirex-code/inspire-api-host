from app.core.errors import ConflictError, NotFoundError, ValidationError
from . import core, ledger
from .db import audit, one, rows


async def bank_import(c,account,raw,actor):
    if account not in core.CASH: raise ValidationError('Choose a cash or bank account')
    data=core.parse_bank_csv(raw); inserted=0
    for r in data:
        uid=await c.fetchval('''INSERT INTO bank_statement_lines(bank_account,txn_date,description,amount,external_ref)
            VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING RETURNING id''',account,r['txn_date'],r['description'],r['amount'],r['external_ref'])
        inserted+=int(uid is not None)
    await audit(c,actor,'import','bank_statement',account,dict(received=len(data),inserted=inserted))
    return dict(received=len(data),inserted=inserted,duplicates=len(data)-inserted)


async def bank_summary(c,account):
    r=await one(c,"""SELECT COALESCE(SUM(amount),0) AS statement_movement,COUNT(*) FILTER(WHERE status='unmatched') AS unmatched,
        COUNT(*) AS total,COALESCE(SUM(amount) FILTER(WHERE status='unmatched'),0) AS unmatched_amount
        FROM bank_statement_lines WHERE bank_account=$1""",account)
    data=await ledger.account_balances(c)
    balances={a['code']:core.natural(a['type'],a['debit'],a['credit']) for a in data}
    if account not in balances: raise NotFoundError('Account not found')
    return dict(r,ledger_balance=balances[account],payhere_clearing=balances['1030'])


async def bank_lines(c,account):
    return await rows(c,'''SELECT s.*,j.number AS journal_number,
        CASE WHEN s.status='unmatched' THEN (SELECT COALESCE(jsonb_agg(candidate),'[]') FROM (
            SELECT j.id,j.number,j.entry_date,j.memo FROM journals j JOIN journal_lines l ON l.journal_id=j.id
            WHERE l.account_code=s.bank_account AND ABS(j.entry_date-s.txn_date)<=5
            AND NOT EXISTS(SELECT 1 FROM bank_statement_lines b WHERE b.journal_id=j.id)
            GROUP BY j.id HAVING SUM(l.debit-l.credit)=s.amount ORDER BY j.entry_date LIMIT 3
        ) candidate) ELSE '[]'::jsonb END AS suggestions
        FROM bank_statement_lines s LEFT JOIN journals j ON j.id=s.journal_id
        WHERE s.bank_account=$1 ORDER BY s.txn_date DESC,s.id''',account)


async def bank_action(c,uid,action,b,actor):
    line=await one(c,'SELECT * FROM bank_statement_lines WHERE id=$1 FOR UPDATE',uid)
    if not line: raise NotFoundError('Statement line not found')
    if line['status']!='unmatched': raise ConflictError('Statement line is already '+line['status'])
    if action=='ignore':
        await c.execute("UPDATE bank_statement_lines SET status='ignored' WHERE id=$1",uid)
        await audit(c,actor,'ignore','bank_line',uid); return dict(ok=True)
    if action=='match':
        jid=int(b['journal_id'])
        if await c.fetchval('SELECT 1 FROM bank_statement_lines WHERE journal_id=$1',jid): raise ConflictError('Journal already matched')
        mv=await c.fetchval('SELECT COALESCE(SUM(debit-credit),0) FROM journal_lines WHERE journal_id=$1 AND account_code=$2',jid,line['bank_account'])
        if mv!=line['amount']: raise ValidationError('Journal bank movement does not match the statement')
        result=dict(ok=True)
    else:
        amount=abs(line['amount']); account=line['bank_account']
        if action=='payhere-settlement':
            if line['amount']<=0: raise ValidationError('Choose a deposit')
            fee=core.money(b.get('fee',0))
            if fee<0: raise ValidationError('Fee cannot be negative')
            if amount+fee>await ledger.balance(c,'1030'): raise ValidationError('Settlement exceeds PayHere clearing')
            lines=[dict(account_code=account,debit=amount),dict(account_code='1030',credit=amount+fee)]
            if fee: lines.insert(1,dict(account_code='6060',debit=fee))
        elif action=='post':
            code=b.get('account_code')
            if code==account: raise ValidationError('Choose a different account')
            lines=[dict(account_code=account,debit=amount),dict(account_code=code,credit=amount)] if line['amount']>0 else [dict(account_code=code,debit=amount),dict(account_code=account,credit=amount)]
        else: raise NotFoundError('Bank action not found')
        result=await ledger.post(c,dict(entry_date=line['txn_date'],memo=b.get('memo') or line['description'],source='bank',source_ref=line['external_ref'],lines=lines,
            **({'cash_flow_category':'learners'} if action=='payhere-settlement' else {})),actor)
        jid=result['id']
    await c.execute("UPDATE bank_statement_lines SET status='matched',journal_id=$1 WHERE id=$2",jid,uid)
    await audit(c,actor,action,'bank_line',uid,dict(journal_id=jid))
    return result


async def create_supplier(c,b,actor):
    r=await one(c,'INSERT INTO suppliers(name,kind,email,phone) VALUES($1,$2,$3,$4) RETURNING *',b['name'],b.get('kind','vendor'),b.get('email') or None,b.get('phone'))
    await audit(c,actor,'create','supplier',r['id']); return r


async def create_bill(c,b,actor):
    amount=core.money(b['amount'],True)
    a=await one(c,'SELECT * FROM accounts WHERE code=$1',b['expense_account'])
    if not a or a['type'] not in {'cost_of_delivery','expense','asset'}: raise ValidationError('Choose a cost, expense or asset account')
    s=await one(c,'SELECT * FROM suppliers WHERE id=$1',b['supplier_id'])
    if not s: raise NotFoundError('Supplier not found')
    n=await c.fetchval("SELECT nextval('bill_number_seq')"); number=f'BILL-{n}'
    j=await ledger.post(c,dict(entry_date=b['bill_date'],memo=f"{number} — {s['name']}: {b['description']}",source='bills',source_ref=number,
        lines=[dict(account_code=a['code'],debit=amount),dict(account_code='2010',credit=amount)]),actor)
    return await one(c,'''INSERT INTO bills(number,supplier_id,supplier_ref,bill_date,due_date,expense_account,description,amount,journal_id)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *''',number,s['id'],b.get('supplier_ref'),b['bill_date'],b.get('due_date') or b['bill_date'],a['code'],b['description'],amount,j['id'])


async def pay_bill(c,uid,b,actor):
    bill=await one(c,'SELECT b.*,s.name AS supplier FROM bills b JOIN suppliers s ON s.id=b.supplier_id WHERE b.id=$1 FOR UPDATE OF b',uid)
    if not bill: raise NotFoundError('Bill not found')
    if bill['status']!='open': raise ConflictError('Bill is '+bill['status'])
    amount=core.money(b.get('amount') if b.get('amount') is not None else bill['amount']-bill['amount_paid'],True)
    if amount>bill['amount']-bill['amount_paid']: raise ValidationError('Payment exceeds the amount owed')
    bank=b.get('bank_account','1020')
    if bank not in core.CASH: raise ValidationError('Choose a cash or bank account')
    category='capex' if bill['expense_account']=='1500' else 'marketing' if bill['expense_account']=='6030' else 'premises' if bill['expense_account'] in {'6040','6050'} else 'suppliers'
    j=await ledger.post(c,dict(entry_date=b.get('paid_date') or core.today(),memo=f"Payment of {bill['number']} — {bill['supplier']}",source='bills',source_ref=bill['number'],cash_flow_category=category,
        lines=[dict(account_code='2010',debit=amount),dict(account_code=bank,credit=amount)]),actor)
    paid=bill['amount_paid']+amount
    await c.execute('UPDATE bills SET amount_paid=$1,status=$2 WHERE id=$3',paid,'paid' if paid==bill['amount'] else 'open',uid)
    return j


async def bills(c):
    data=await rows(c,'SELECT b.*,s.name AS supplier,s.kind AS supplier_kind,a.name AS account_name FROM bills b JOIN suppliers s ON s.id=b.supplier_id JOIN accounts a ON a.code=b.expense_account ORDER BY (b.status=\'open\') DESC,b.due_date')
    return [dict(b,days_overdue=max(0,core.days(b['due_date'],core.today())) if b['status']=='open' else 0) for b in data]
