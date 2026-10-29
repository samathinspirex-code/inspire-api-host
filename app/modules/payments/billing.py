import secrets
from datetime import datetime, timedelta, timezone

from app.core.errors import ConflictError, NotFoundError, ValidationError
from . import core, ledger
from .db import audit, one, rows


async def settings(c):
    import copy
    result = copy.deepcopy(core.DEFAULTS)
    result.update({r['key']: r['value'] for r in await rows(c, 'SELECT * FROM settings')})
    return result


async def create_learner(c, b, actor):
    n = await c.fetchval("SELECT nextval('learner_number_seq')")
    r = await one(c, '''INSERT INTO learners(learner_no,first_name,last_name,email,phone,address,city,lms_ref)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *''', f'S{n}', b['first_name'], b['last_name'], b.get('email') or None,
        b.get('phone') or None, b.get('address'), b.get('city', 'Colombo'), b.get('lms_ref'))
    await audit(c, actor, 'create', 'learner', r['id'])
    return r


async def invoice(c, b, actor):
    amount = core.money(b['amount'], True)
    kind = b['kind']
    if kind not in {'registration','instalment','course','late_fee','other'}: raise ValidationError('Unknown invoice kind')
    if kind in {'instalment','course'} and not b.get('enrolment_id'): raise ValidationError('Tuition needs an enrolment')
    account = {'registration':'4040', 'late_fee':'4050', 'other':b.get('revenue_account', '4090')}.get(kind, '2100')
    if kind == 'other':
        a = await one(c, 'SELECT * FROM accounts WHERE code=$1', account)
        if not a or a['type'] != 'income': raise ValidationError('Choose an income account')
    n = await c.fetchval("SELECT nextval('invoice_number_seq')")
    number = f'INV-{n}'
    tags = dict(learner_id=b['learner_id'], enrolment_id=b.get('enrolment_id'))
    j = await ledger.post(c, dict(entry_date=b.get('issue_date', core.today()), memo=number + ' — ' + b['description'], source='billing', source_ref=number,
        lines=[dict(account_code='1100', debit=amount, **tags), dict(account_code=account, credit=amount, **tags)]), actor)
    r = await one(c, '''INSERT INTO invoices(number,learner_id,enrolment_id,instalment_id,kind,description,issue_date,due_date,amount,journal_id)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *''', number, b['learner_id'], b.get('enrolment_id'), b.get('instalment_id'), kind,
        b['description'], b.get('issue_date', core.today()), b.get('due_date', core.today()), amount, j['id'])
    if b.get('instalment_id'): await c.execute("UPDATE instalments SET status='invoiced',invoice_id=$1 WHERE id=$2", r['id'], b['instalment_id'])
    return r


async def enrol(c, b, actor):
    p = await one(c, 'SELECT * FROM programmes WHERE code=$1 AND active', b['programme_code'])
    if not p: raise NotFoundError('Programme not found')
    total = core.money(b.get('total_fee') if b.get('total_fee') is not None else p['default_fee'], True)
    n = b.get('instalments') or p['default_instalments']
    amounts = core.split_instalments(total, n)
    r = await one(c, '''INSERT INTO enrolments(learner_id,programme_code,start_date,total_fee,instalments,counsellor)
        VALUES($1,$2,$3,$4,$5,$6) RETURNING *''', b['learner_id'], p['code'], b['start_date'], total, n, b.get('counsellor'))
    first = b.get('first_due_date') or b['start_date']
    for i, amount in enumerate(amounts):
        await c.execute('INSERT INTO instalments(enrolment_id,seq,due_date,amount) VALUES($1,$2,$3,$4)', r['id'], i+1, core.add_months(first, i), amount)
    reg = core.money(b.get('registration_fee') if b.get('registration_fee') is not None else (await settings(c))['registration_fee'])
    inv = None
    if reg > 0:
        inv = await invoice(c, dict(learner_id=b['learner_id'], enrolment_id=r['id'], kind='registration', description='Registration fee — ' + p['name'],
            issue_date=core.today(), due_date=core.today(), amount=reg), actor)
    await audit(c, actor, 'enrol', 'enrolment', r['id'])
    return dict(enrolment=r, registration_invoice=inv)


async def invoice_instalment(c, instalment_id, actor, as_of=None):
    it = await one(c, '''SELECT i.*,e.learner_id,e.instalments AS n,p.name AS programme FROM instalments i
        JOIN enrolments e ON e.id=i.enrolment_id JOIN programmes p ON p.code=e.programme_code WHERE i.id=$1 FOR UPDATE OF i''', instalment_id)
    if not it: raise NotFoundError('Instalment not found')
    if it['invoice_id']: return await one(c, 'SELECT * FROM invoices WHERE id=$1', it['invoice_id'])
    if it['status'] != 'scheduled': raise ConflictError('Instalment is not scheduled')
    return await invoice(c, dict(learner_id=it['learner_id'], enrolment_id=it['enrolment_id'], instalment_id=it['id'],
        kind='course' if it['n']==1 else 'instalment', description=('Course fee' if it['n']==1 else f"Instalment {it['seq']}/{it['n']}") + ' — ' + it['programme'],
        issue_date=as_of or core.today(), due_date=it['due_date'], amount=it['amount']), actor)


async def receipt(c, b, actor):
    amount = core.money(b['amount'], True)
    inv = await one(c, 'SELECT * FROM invoices WHERE id=$1 FOR UPDATE', b['invoice_id']) if b.get('invoice_id') else None
    if b.get('invoice_id') and not inv: raise NotFoundError('Invoice not found')
    if inv and inv['status'] != 'open': raise ConflictError('Invoice is ' + inv['status'])
    learner_id = inv['learner_id'] if inv else b.get('learner_id')
    if not learner_id: raise ValidationError('Choose an invoice or learner')
    method = b.get('method', 'bank_transfer')
    if method not in {'cash','bank_transfer','payhere'}: raise ValidationError('Unknown receipt method')
    applied = min(amount, inv['amount']-inv['amount_paid']) if inv else core.ZERO
    excess = amount-applied
    tags = dict(learner_id=learner_id, enrolment_id=inv['enrolment_id'] if inv else None)
    lines = [dict(account_code={'cash':'1010','bank_transfer':'1020','payhere':'1030'}[method], debit=amount, **tags)]
    if applied: lines.append(dict(account_code='1100', credit=applied, **tags))
    if excess: lines.append(dict(account_code='2110', credit=excess, learner_id=learner_id))
    received = b.get('received_date') or core.today()
    j = await ledger.post(c, dict(entry_date=received, memo=b.get('memo') or ('Receipt for ' + (inv['number'] if inv else 'learner advance')),
        source='payhere' if method == 'payhere' else 'receipt', idempotency_key='payhere:'+b['gateway_payment_id'] if b.get('gateway_payment_id') else None, lines=lines), actor)
    if j.get('duplicate'): return dict(duplicate=True, journal=j)
    pay = await one(c, '''INSERT INTO payments(invoice_id,learner_id,amount,method,gateway_payment_id,received_date,journal_id)
        VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING *''', inv['id'] if inv else None, learner_id, amount, method, b.get('gateway_payment_id'), received, j['id'])
    if inv:
        paid = inv['amount_paid']+applied
        status = 'paid' if paid >= inv['amount'] else 'open'
        await c.execute('UPDATE invoices SET amount_paid=$1,status=$2 WHERE id=$3', paid, status, inv['id'])
        if status == 'paid':
            await c.execute("UPDATE instalments SET status='paid' WHERE invoice_id=$1", inv['id'])
            await c.execute("UPDATE payment_links SET status='paid',paid_at=now() WHERE invoice_id=$1 AND status='sent'", inv['id'])
    return dict(payment=pay, journal=j, applied=applied, excess=excess)


async def learner_detail(c, value):
    l = await one(c, 'SELECT * FROM learners WHERE learner_no=$1 OR id::text=$1', value)
    if not l: raise NotFoundError('Learner not found')
    uid = l['id']
    l['enrolments'] = await rows(c, 'SELECT e.*,p.name AS programme FROM enrolments e JOIN programmes p ON p.code=e.programme_code WHERE learner_id=$1 ORDER BY created_at', uid)
    l['instalments'] = await rows(c, 'SELECT i.* FROM instalments i JOIN enrolments e ON e.id=i.enrolment_id WHERE e.learner_id=$1 ORDER BY due_date,seq', uid)
    for key, order in [('invoices','issue_date'), ('payments','received_date'), ('links','created_at')]:
        l[key] = await rows(c, f"SELECT * FROM {'payment_links' if key=='links' else key} WHERE learner_id=$1 ORDER BY {order} DESC", uid)
    return l


async def learners(c, as_of=None):
    as_of = as_of or core.today()
    # One bounded set of queries rather than one query per learner.
    data = await one(c, '''SELECT
        (SELECT COALESCE(jsonb_agg(x),'[]') FROM (SELECT * FROM learners ORDER BY learner_no) x) AS learners,
        (SELECT COALESCE(jsonb_agg(x),'[]') FROM (SELECT e.*,p.name AS programme FROM enrolments e JOIN programmes p ON p.code=e.programme_code ORDER BY created_at,id) x) AS enrolments,
        (SELECT COALESCE(jsonb_agg(x),'[]') FROM (SELECT * FROM invoices WHERE issue_date <= $1) x) AS invoices,
        (SELECT COALESCE(jsonb_agg(x),'[]') FROM (SELECT * FROM instalments ORDER BY seq) x) AS instalments''', as_of)
    ls, es, invs, its = (data[k] for k in ['learners','enrolments','invoices','instalments'])
    by_enrol, by_inv, by_inst = {}, {}, {}
    for e in es: by_enrol[e['learner_id']] = e
    for i in invs: by_inv.setdefault(i['learner_id'], []).append(i)
    for i in its: by_inst.setdefault(i['enrolment_id'], []).append(i)
    result=[]
    for l in ls:
        e=by_enrol.get(l['id']); invoices=by_inv.get(l['id'], []); instalments=by_inst.get(e['id'], []) if e else []
        paid=sum((i['amount_paid'] for i in invoices if e and i['enrolment_id']==e['id'] and i['kind'] in {'course','instalment'}), core.ZERO)
        open_invs=[i for i in invoices if i['status']=='open']; overdue=[i for i in open_invs if i['due_date']<as_of]
        open_total=sum((i['amount']-i['amount_paid'] for i in open_invs),core.ZERO)
        balance=(open_total if e['status']=='withdrawn' else e['total_fee']-paid) if e else core.ZERO
        d=max((core.days(i['due_date'],as_of) for i in overdue),default=0)
        ip=sum(i['status']=='paid' for i in instalments); nxt=next((i for i in instalments if i['status'] in {'scheduled','invoiced'}),None)
        status='withdrawn' if e and e['status']=='withdrawn' else 'paid' if balance<=0 else 'at_risk' if d>60 else 'overdue' if d else 'new' if not ip else 'current'
        result.append(dict(l,name=l['first_name']+' '+l['last_name'], enrolment_id=e['id'] if e else None,enrolment_status=e['status'] if e else None,
            programme=e['programme'] if e else None,total_fee=e['total_fee'] if e else None,instalments=e['instalments'] if e else None,
            counsellor=e['counsellor'] if e else None,paid=paid,balance=balance,instalments_paid=ip,next_due=nxt['due_date'] if nxt else None,
            next_amount=nxt['amount'] if nxt else None,days_overdue=d,status=status,open_invoiced=open_total,
            overdue_amount=sum((i['amount']-i['amount_paid'] for i in overdue),core.ZERO)))
    return result


async def enqueue(c, channel, to, body, subject=None, link_id=None, template=None):
    if not to: raise ValidationError('The learner needs an email address or phone number for this channel')
    return await c.fetchval('INSERT INTO message_outbox(channel,to_addr,subject,body,payment_link_id,template) VALUES($1,$2,$3,$4,$5,$6) RETURNING id',
        channel,to,subject,body,link_id,__import__('json').dumps(template) if template else None)


def link_url(link):
    from app.core.config import settings as config
    return config.PAYMENTS_PUBLIC_BASE_URL.rstrip('/')+'/pay/'+link['token']


async def create_link(c,b,actor):
    inv=await one(c,'SELECT * FROM invoices WHERE id=$1 FOR UPDATE',b['invoice_id']) if b.get('invoice_id') else None
    if not inv and b.get('invoice_id'): raise NotFoundError('Invoice not found')
    if not inv:
        if b.get('kind') in {'instalment','course'}:
            it=await one(c,"SELECT i.* FROM instalments i JOIN enrolments e ON e.id=i.enrolment_id WHERE e.learner_id=$1 AND e.status='active' AND i.status IN ('scheduled','invoiced') ORDER BY i.due_date,i.seq LIMIT 1 FOR UPDATE OF i",b.get('learner_id'))
            if not it: raise ConflictError('No outstanding instalments')
            inv=await invoice_instalment(c,it['id'],actor)
        elif b.get('kind')=='registration':
            inv=await one(c,"SELECT * FROM invoices WHERE learner_id=$1 AND kind='registration' AND status='open' ORDER BY due_date LIMIT 1 FOR UPDATE",b.get('learner_id'))
            if not inv: raise ConflictError('There is no unpaid registration invoice for this learner')
        else:
            inv=await invoice(c,dict(b,description=b.get('description') or b.get('kind','Fee'),issue_date=core.today()),actor)
    if inv['status']!='open': raise ConflictError('Invoice is '+inv['status'])
    if await c.fetchval("SELECT 1 FROM payment_links WHERE invoice_id=$1 AND status='sent'",inv['id']): raise ConflictError('An active link already exists for this invoice')
    amount=min(core.money(b['amount'],True),inv['amount']-inv['amount_paid']) if b.get('invoice_id') and b.get('amount') is not None else inv['amount']-inv['amount_paid']
    core.money(amount,True)
    l=await one(c,'SELECT * FROM learners WHERE id=$1',inv['learner_id'])
    n=await c.fetchval("SELECT nextval('payment_link_seq')")
    s=await settings(c); channel=b.get('channel','email'); expiry=datetime.now(timezone.utc)+timedelta(days=s['link_expiry_days'])
    r=await one(c,'''INSERT INTO payment_links(code,token,invoice_id,learner_id,amount,purpose,channel,due_date,expires_at,created_by)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *''',f'PL-{n}',secrets.token_urlsafe(24),inv['id'],l['id'],amount,
        inv['description'].split(' — ')[0],channel,b.get('due_date') or inv['due_date'],expiry,actor)
    msg=f"Hi {l['first_name']},\nYour Inspire College {r['purpose'].lower()} of LKR {amount:,.2f} is due on {r['due_date']}.\nPay securely:\n{link_url(r)}\nThank you, Inspire College Accounts"
    await enqueue(c,channel,l['email'] if channel=='email' else l['phone'],msg,'Inspire College — '+r['purpose'],r['id'],[l['first_name'],r['purpose'],f'LKR {amount:,.2f}',r['due_date'],link_url(r)])
    await audit(c,actor,'create','payment_link',r['id'])
    return dict(r,url=link_url(r),invoice_number=inv['number'],message_preview=msg,delivery_status='queued')


async def list_links(c,status=None):
    data=await rows(c,'''SELECT pl.*,l.learner_no,l.first_name||' '||l.last_name AS learner_name,i.number AS invoice_number
        FROM payment_links pl JOIN learners l ON l.id=pl.learner_id JOIN invoices i ON i.id=pl.invoice_id
        WHERE ($1::text IS NULL OR pl.status=$1) ORDER BY pl.created_at DESC LIMIT 1000''',status)
    return [dict(l,url=link_url(l)) for l in data]


async def withdraw(c,uid,b,actor):
    e=await one(c,'SELECT * FROM enrolments WHERE id=$1 FOR UPDATE',uid)
    if not e: raise NotFoundError('Enrolment not found')
    if e['status']!='active': raise ConflictError('Enrolment is not active')
    d=core.day(b['date']); await ledger.ensure_open(c,d)
    await c.execute("UPDATE instalments SET status='cancelled' WHERE enrolment_id=$1 AND status='scheduled'",uid)
    invs=await rows(c,"SELECT * FROM invoices WHERE enrolment_id=$1 AND status='open' AND kind IN ('course','instalment') FOR UPDATE",uid)
    tags=dict(learner_id=e['learner_id'],enrolment_id=uid)
    for inv in invs:
        amount=inv['amount']-inv['amount_paid']
        if amount>0: await ledger.post(c,dict(entry_date=d,memo='Withdrawal: cancel '+inv['number'],source='billing',lines=[dict(account_code='2100',debit=amount,**tags),dict(account_code='1100',credit=amount,**tags)]),actor)
        await c.execute("UPDATE invoices SET status='cancelled',cancelled_on=$2 WHERE id=$1",inv['id'],d)
        await c.execute("UPDATE instalments SET status='cancelled' WHERE invoice_id=$1",inv['id'])
        await c.execute("UPDATE payment_links SET status='cancelled' WHERE invoice_id=$1 AND status='sent'",inv['id'])
    refund=core.money(b.get('refund_amount',0))
    if refund<0: raise ValidationError('Refund cannot be negative')
    if refund:
        paid=await c.fetchval('SELECT COALESCE(SUM(amount_paid),0) FROM invoices WHERE enrolment_id=$1',uid)
        if refund>paid: raise ValidationError('Refund exceeds fees received')
        await ledger.post(c,dict(entry_date=d,memo='Withdrawal refund approved: '+b.get('reason',''),source='billing',lines=[dict(account_code='2100',debit=refund,**tags),dict(account_code='2400',credit=refund,**tags)]),actor)
    bal=await c.fetchval("SELECT COALESCE(SUM(credit-debit),0) FROM journal_lines WHERE enrolment_id=$1 AND account_code='2100'",uid)
    revenue=await c.fetchval('SELECT revenue_account FROM programmes WHERE code=$1',e['programme_code'])
    if bal:
        lines=[dict(account_code='2100',debit=bal,**tags),dict(account_code=revenue,credit=bal)] if bal>0 else [dict(account_code='6080',debit=-bal),dict(account_code='2100',credit=-bal,**tags)]
        await ledger.post(c,dict(entry_date=d,memo='Withdrawal: settle contract',source='billing',lines=lines),actor)
    await c.execute("UPDATE enrolments SET status='withdrawn',withdrawn_on=$1 WHERE id=$2",d,uid)
    await audit(c,actor,'withdraw','enrolment',uid,b)
    return dict(cancelled_invoices=len(invs))
