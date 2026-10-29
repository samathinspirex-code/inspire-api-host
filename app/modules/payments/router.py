import csv
import hashlib
import io
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import FileResponse, Response

from app.core.config import settings
from app.core.errors import ForbiddenError, ValidationError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from . import billing, closing, core, ledger, operations, payhere, reports, setup, schemas as S
from .db import audit, dumps, one, rows, transaction
from .rate_limit import check as rate_limit


async def finance(user: CurrentUser = Depends(get_current_user)):
    # Deliberately require the explicit Payments grant, even for administrators.
    if 'FINANCE' not in user.access: raise ForbiddenError('Payments access is required')
    return user


async def admin(user: CurrentUser = Depends(finance)):
    if not {'ADMIN','SUPER_ADMIN'}.intersection(user.access): raise ForbiddenError('Payments and administrator access are required')
    return user


async def database(request: Request, user=Depends(finance)):
    async with transaction(write=request.method!='GET') as c:
        yield c


router=APIRouter(prefix='/api/v1/cms/payments',tags=['Payments'],dependencies=[Depends(finance)])
public_router=APIRouter(tags=['Public payments'])


async def once(c,request,user,payload,fn):
    key=request.headers.get('Idempotency-Key','')
    if not key or len(key)>120: raise ValidationError('Provide an Idempotency-Key of at most 120 characters for this operation')
    key=f'{user.user_id}:{key}'
    fingerprint=hashlib.sha256((request.url.path+'\n'+dumps(payload)).encode()).hexdigest()
    old=await one(c,'SELECT * FROM request_keys WHERE key=$1',key)
    if old:
        if old['fingerprint']!=fingerprint: raise ValidationError('This Idempotency-Key was already used for a different request')
        return old['response']
    result=await fn()
    await c.execute('INSERT INTO request_keys(key,actor,fingerprint,response) VALUES($1,$2,$3,$4)',key,user.email,fingerprint,result)
    return result


@router.get('/me')
async def me(user=Depends(finance),c=Depends(database,scope="function")):
    demo=await c.fetchval('SELECT demo_data FROM module_state WHERE id=true')
    return dict(id=user.user_id,email=user.email,name=user.email,role='admin' if {'ADMIN','SUPER_ADMIN'}.intersection(user.access) else 'accountant',demo_data=demo)


@router.post('/sample-data/clear')
async def clear_sample_data(b:dict=Body(...),user=Depends(admin),c=Depends(database,scope="function")):
    if b.get('confirmation') != 'CLEAR SAMPLE DATA':
        raise ValidationError('Type CLEAR SAMPLE DATA to confirm')
    return await setup.clear_sample_ledger(c,user.email)


@router.get('/dashboard')
async def dashboard(as_of: str | None=None,c=Depends(database,scope="function")):
    return await reports.dashboard(c,as_of)


@router.get('/accounts')
async def accounts(as_of: str | None=None,c=Depends(database,scope="function")):
    data=await ledger.account_balances(c,core.day(as_of or core.today()))
    return [dict(a,balance=core.natural(a['type'],a['debit'],a['credit'])) for a in data]


@router.post('/accounts',status_code=201)
async def create_account(b:S.Account,user=Depends(admin),c=Depends(database,scope="function")):
    groups={'asset':{'CA','NCA'},'contra_asset':{'CA','NCA'},'liability':{'CL','NCL'},'equity':{'EQ'},'income':{'REV'},'cost_of_delivery':{'COD'},'expense':{'OPEX'}}
    if b.grp not in groups[b.type]: raise ValidationError('Account type and group do not match')
    r=await one(c,'INSERT INTO accounts(code,name,type,grp) VALUES($1,$2,$3,$4) RETURNING *',b.code,b.name,b.type,b.grp)
    await audit(c,user.email,'create','account',b.code); return r


@router.patch('/accounts/{code}')
async def patch_account(code:str,b:S.AccountPatch,user=Depends(admin),c=Depends(database,scope="function")):
    from app.core.errors import NotFoundError
    a=await one(c,'SELECT * FROM accounts WHERE code=$1',code)
    if not a: raise NotFoundError('Account not found')
    if a['is_system'] and b.is_active is False: raise ValidationError('System accounts cannot be deactivated')
    await c.execute('UPDATE accounts SET name=COALESCE($1,name),is_active=COALESCE($2,is_active) WHERE code=$3',b.name,b.is_active,code)
    await audit(c,user.email,'update','account',code,b.data()); return dict(ok=True)


@router.get('/journals')
async def journals(request:Request,limit:int=Query(200,ge=1,le=1000),offset:int=Query(0,ge=0),c=Depends(database,scope="function")):
    q=request.query_params
    return await ledger.list_journals(c,core.day(q['from']) if q.get('from') else None,core.day(q['to']) if q.get('to') else None,q.get('account'),limit,offset)


@router.post('/journals',status_code=201)
async def journal(request:Request,b:S.Journal,user=Depends(finance),c=Depends(database,scope="function")):
    return await once(c,request,user,b.data(),lambda:ledger.post(c,b.data(),user.email))


@router.post('/journals/{uid}/reverse',status_code=201)
async def reversal(uid:int,b:S.Reversal,user=Depends(finance),c=Depends(database,scope="function")):
    return await ledger.reverse(c,uid,b.entry_date.isoformat(),b.reason,user.email)


@router.get('/programmes')
async def programmes(c=Depends(database,scope="function")):
    return await rows(c,'SELECT * FROM programmes ORDER BY name')


@router.post('/programmes',status_code=201)
async def programme(b:S.Programme,user=Depends(admin),c=Depends(database,scope="function")):
    if await c.fetchval('SELECT type::text FROM accounts WHERE code=$1',b.revenue_account)!='income': raise ValidationError('Choose an income account')
    r=await one(c,'''INSERT INTO programmes(code,name,revenue_account,teaching_months,default_fee,default_instalments) VALUES($1,$2,$3,$4,$5,$6)
        ON CONFLICT(code) DO UPDATE SET name=EXCLUDED.name,revenue_account=EXCLUDED.revenue_account,teaching_months=EXCLUDED.teaching_months,
        default_fee=EXCLUDED.default_fee,default_instalments=EXCLUDED.default_instalments RETURNING *''',b.code,b.name,b.revenue_account,b.teaching_months,b.default_fee,b.default_instalments)
    await audit(c,user.email,'save','programme',b.code); return r


@router.get('/learners')
async def learners(as_of:str | None=None,c=Depends(database,scope="function")):
    return await billing.learners(c,core.day(as_of) if as_of else None)


@router.post('/learners',status_code=201)
async def learner(request:Request,b:S.Learner,user=Depends(finance),c=Depends(database,scope="function")):
    return await once(c,request,user,b.data(),lambda:billing.create_learner(c,b.data(),user.email))


@router.post('/learner-enrolments',status_code=201)
async def learner_enrolment(request:Request,b:S.EnrolLearner,user=Depends(finance),c=Depends(database,scope="function")):
    async def create():
        l=await billing.create_learner(c,b.model_dump(mode='json',exclude_none=True,exclude={'enrolment'}),user.email)
        e=S.Enrolment.model_validate(dict(b.enrolment.data(),learner_id=l['id']))
        r=await billing.enrol(c,e.data(),user.email)
        return dict(learner_no=l['learner_no'],learner=l,**r)
    return await once(c,request,user,b.data(),create)


@router.get('/learners/{uid}')
async def learner_detail(uid:str,c=Depends(database,scope="function")):
    return await billing.learner_detail(c,uid)


@router.post('/integrations/lms/enrolment',status_code=201)
async def lms_enrolment(request:Request,b:S.LmsEnrolment,user=Depends(finance),c=Depends(database,scope="function")):
    async def create():
        l=await one(c,'SELECT * FROM learners WHERE lms_ref=$1',b.lms_ref)
        if not l: l=await billing.create_learner(c,b.model_dump(mode='json',exclude_none=True,exclude={'enrolment'}),user.email)
        e=S.Enrolment.model_validate(dict(b.enrolment.data(),learner_id=l['id']))
        return dict(learner_no=l['learner_no'],**await billing.enrol(c,e.data(),user.email))
    return await once(c,request,user,b.data(),create)


@router.post('/enrolments',status_code=201)
async def enrolment(request:Request,b:S.Enrolment,user=Depends(finance),c=Depends(database,scope="function")):
    return await once(c,request,user,b.data(),lambda:billing.enrol(c,b.data(),user.email))


@router.post('/enrolments/{uid}/withdraw')
async def withdraw(uid:UUID,b:S.Withdrawal,user=Depends(finance),c=Depends(database,scope="function")):
    return await billing.withdraw(c,str(uid),b.data(),user.email)


@router.get('/invoices')
async def invoices(status:str | None=None,learner_id:UUID | None=None,c=Depends(database,scope="function")):
    return await rows(c,'''SELECT i.*,l.learner_no,l.first_name||' '||l.last_name AS learner_name FROM invoices i JOIN learners l ON l.id=i.learner_id
        WHERE ($1::text IS NULL OR i.status=$1) AND ($2::uuid IS NULL OR i.learner_id=$2) ORDER BY issue_date DESC LIMIT 1000''',status,str(learner_id) if learner_id else None)


@router.post('/invoices',status_code=201)
async def invoice(request:Request,b:S.Invoice,user=Depends(finance),c=Depends(database,scope="function")):
    data=b.data(); data.setdefault('issue_date',core.today()); data.setdefault('due_date',data['issue_date'])
    return await once(c,request,user,data,lambda:billing.invoice(c,data,user.email))


@router.post('/receipts',status_code=201)
async def receipt(request:Request,b:S.Receipt,user=Depends(finance),c=Depends(database,scope="function")):
    return await once(c,request,user,b.data(),lambda:billing.receipt(c,b.data(),user.email))


@router.get('/payment-links')
async def links(status:str | None=None,c=Depends(database,scope="function")):
    return await billing.list_links(c,status)


@router.post('/payment-links',status_code=201)
async def link(request:Request,b:S.Link,user=Depends(finance),c=Depends(database,scope="function")):
    return await once(c,request,user,b.data(),lambda:billing.create_link(c,b.data(),user.email))


@router.post('/payment-links/{uid}/cancel')
async def cancel_link(uid:UUID,user=Depends(finance),c=Depends(database,scope="function")):
    await c.execute("UPDATE payment_links SET status='cancelled' WHERE id=$1 AND status='sent'",str(uid))
    await audit(c,user.email,'cancel','payment_link',uid); return dict(ok=True)


@router.post('/payment-links/{uid}/simulate')
async def simulate(uid:UUID,user=Depends(finance),c=Depends(database,scope="function")):
    return await payhere.simulate(c,str(uid),user.email)


@router.get('/banking/{account}/summary')
async def bank_summary(account:str,c=Depends(database,scope="function")):
    return await operations.bank_summary(c,account)


@router.get('/banking/{account}/lines')
async def bank_lines(account:str,c=Depends(database,scope="function")):
    return await operations.bank_lines(c,account)


@router.post('/banking/{account}/import',status_code=201)
async def bank_import(account:str,request:Request,user=Depends(finance),c=Depends(database,scope="function")):
    raw=await request.body()
    if len(raw)>2*1024*1024: raise ValidationError('CSV file must be smaller than 2 MB')
    try: raw=raw.decode('utf-8-sig')
    except UnicodeDecodeError: raise ValidationError('Save the CSV as UTF-8')
    return await operations.bank_import(c,account,raw,user.email)


@router.post('/banking/lines/{uid}/{action}')
async def bank_action(uid:UUID,action:str,b:dict=Body(default={}),user=Depends(finance),c=Depends(database,scope="function")):
    from pydantic import ValidationError as PydanticError
    model={'post':S.BankPost,'match':S.BankMatch,'payhere-settlement':S.Settlement}.get(action)
    try: data=model.model_validate(b).data() if model else {}
    except PydanticError: raise ValidationError('Check the bank action fields')
    return await operations.bank_action(c,str(uid),action,data,user.email)


@router.get('/suppliers')
async def suppliers(c=Depends(database,scope="function")):
    return await rows(c,'SELECT * FROM suppliers ORDER BY name')


@router.post('/suppliers',status_code=201)
async def supplier(request:Request,b:S.Supplier,user=Depends(finance),c=Depends(database,scope="function")):
    return await once(c,request,user,b.data(),lambda:operations.create_supplier(c,b.data(),user.email))


@router.get('/bills')
async def bills(c=Depends(database,scope="function")):
    return await operations.bills(c)


@router.post('/bills',status_code=201)
async def bill(request:Request,b:S.Bill,user=Depends(finance),c=Depends(database,scope="function")):
    return await once(c,request,user,b.data(),lambda:operations.create_bill(c,b.data(),user.email))


@router.post('/bills/{uid}/pay')
async def pay_bill(request:Request,uid:UUID,b:S.BillPayment,user=Depends(finance),c=Depends(database,scope="function")):
    return await once(c,request,user,b.data(),lambda:operations.pay_bill(c,str(uid),b.data(),user.email))


@router.get('/fixed-assets')
async def assets(c=Depends(database,scope="function")):
    return await rows(c,'SELECT * FROM fixed_assets ORDER BY acquired_on')


@router.post('/fixed-assets',status_code=201)
async def asset(request:Request,b:S.Asset,user=Depends(finance),c=Depends(database,scope="function")):
    if b.salvage+b.opening_depreciation>b.cost: raise ValidationError('Salvage and opening depreciation exceed cost')
    async def create():
        r=await one(c,'''INSERT INTO fixed_assets(name,cost,salvage,acquired_on,useful_life_months,opening_depreciation)
            VALUES($1,$2,$3,$4,$5,$6) RETURNING *''',b.name,b.cost,b.salvage,b.acquired_on.isoformat(),b.useful_life_months,b.opening_depreciation)
        await audit(c,user.email,'create','fixed_asset',r['id']); return r
    return await once(c,request,user,b.data(),create)


def report_csv(rep):
    out=io.StringIO(); w=csv.writer(out)
    def safe(v):
        s='' if v is None else str(v)
        return "'"+s if s.startswith(('=','+','-','@','\t','\r')) and not isinstance(v,(int,float,__import__('decimal').Decimal)) else s
    def row(values): w.writerow([safe(v) for v in values])
    row([rep.get('title','General ledger'),rep.get('period','')])
    if 'totals' in rep:
        row(['Code','Account','Debit','Credit'])
        for r in rep['rows']: row([r['code'],r['label'],r['debit'],r['credit']])
        row(['','Totals',rep['totals']['debit'],rep['totals']['credit']])
    elif 'buckets' in rep:
        row(['Reference','Name','Days overdue','Bucket','Amount'])
        for r in rep['rows']: row([r['ref'],r['name'],r['days_overdue'],r['bucket_label'],r['amount']])
    elif 'account' in rep:
        row(['Date','Journal','Memo','Debit','Credit','Balance'])
        for r in rep['lines']: row([r['entry_date'],r['number'],r['memo'],r['debit'],r['credit'],r['running_balance']])
    else:
        for s in rep.get('sections',[]):
            if s.get('heading'): row([s['heading']])
            for l in s['lines']: row([l.get('code',''),l['label'],l['amount']])
            if s.get('total'): row(['',s['total']['label'],s['total']['amount']])
        for g in rep.get('grand',[]): row(['',g['label'],g['amount']])
    return out.getvalue()


@router.get('/reports/{kind}')
async def report(kind:str,request:Request,c=Depends(database,scope="function")):
    rep=await reports.report(c,kind,dict(request.query_params))
    if request.query_params.get('format')=='csv': return Response(report_csv(rep),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="report.csv"'})
    return rep


@router.get('/posting-rules')
async def posting_rules():
    import json
    return json.loads((Path(__file__).parent/'assets/posting-rules.json').read_text(encoding='utf-8'))


@router.post('/close/year-end')
async def year_end(b:S.YearEnd,user=Depends(admin),c=Depends(database,scope="function")):
    return await closing.year_end(c,b.fy_end.isoformat(),user.email)


@router.get('/close/{period}')
async def close_tasks(period:str,c=Depends(database,scope="function")):
    return await closing.tasks(c,period)


@router.put('/close/{period}/tasks/{key}')
async def set_task(period:str,key:str,b:S.Task,user=Depends(finance),c=Depends(database,scope="function")):
    await closing.set_task(c,period,key,b.done,user.email); return dict(ok=True)


@router.post('/close/{period}/run/{what}')
async def run_close(period:str,what:str,dry_run:bool=False,user=Depends(finance),c=Depends(database,scope="function")):
    return await closing.run(c,period,what,user.email,dry_run)


@router.post('/close/{period}/lock')
async def lock(period:str,user=Depends(finance),c=Depends(database,scope="function")):
    await closing.lock(c,period,user.email); return dict(ok=True)


@router.post('/close/{period}/unlock')
async def unlock(period:str,b:S.Reason,user=Depends(admin),c=Depends(database,scope="function")):
    await closing.unlock(c,period,b.reason,user.email); return dict(ok=True)


@router.get('/settings')
async def get_settings(c=Depends(database,scope="function")):
    s=await billing.settings(c)
    return dict(s,demo_data=await c.fetchval('SELECT demo_data FROM module_state WHERE id=true'),messages_enabled=settings.PAYMENTS_MESSAGES_ENABLED,
        payhere=dict(mode=settings.PAYMENTS_PAYHERE_MODE,merchant_id=settings.PAYMENTS_PAYHERE_MERCHANT_ID or None,
                     configured=bool(settings.PAYMENTS_PAYHERE_MERCHANT_ID and settings.PAYMENTS_PAYHERE_MERCHANT_SECRET),
                     notify_url=settings.PAYMENTS_PUBLIC_BASE_URL.rstrip('/')+'/api/v1/payments/payhere/notify'))


@router.put('/settings/{key}')
async def save_setting(key:str,b:dict=Body(...),user=Depends(admin),c=Depends(database,scope="function")):
    from .settings_validation import validate
    value=validate(key,b.get('value'))
    await c.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=now()',key,value)
    await audit(c,user.email,'update','setting',key,dict(value=value)); return dict(ok=True)


@router.get('/audit')
async def audits(user=Depends(admin),c=Depends(database,scope="function")):
    return await rows(c,'SELECT * FROM audit_log ORDER BY id DESC LIMIT 500')


@router.get('/gateway-events')
async def gateway_events(c=Depends(database,scope="function")):
    return await rows(c,'SELECT * FROM gateway_events ORDER BY id DESC LIMIT 200')


@router.get('/messages')
async def messages(c=Depends(database,scope="function")):
    return await rows(c,'SELECT * FROM message_outbox ORDER BY id DESC LIMIT 200')


@public_router.get('/pay/{token}')
async def pay_page(token:str):
    return FileResponse(Path(__file__).parent/'assets/pay.html',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'})


@public_router.get('/api/v1/payments/public/pay/{token}')
async def public_pay(token:str,request:Request):
    rate_limit(request,'view',120)
    async with transaction() as c: return await payhere.public_view(c,token)


@public_router.post('/api/v1/payments/public/pay/{token}/checkout')
async def checkout(token:str,request:Request):
    rate_limit(request,'checkout',20)
    async with transaction(write=True) as c: return await payhere.checkout(c,token)


@public_router.post('/api/v1/payments/payhere/notify')
async def notify(request:Request):
    rate_limit(request,'notify',1200)
    raw=await request.body()
    if len(raw)>32768: raise ValidationError('Notification too large')
    try:
        parsed=parse_qs(raw.decode('utf-8'),keep_blank_values=True,max_num_fields=50)
    except (UnicodeDecodeError,ValueError): raise ValidationError('Invalid notification')
    if any(len(v)!=1 for v in parsed.values()): raise ValidationError('Duplicate notification fields')
    async with transaction(write=True) as c: await payhere.notify(c,{k:v[0] for k,v in parsed.items()})
    return Response('OK',media_type='text/plain')
