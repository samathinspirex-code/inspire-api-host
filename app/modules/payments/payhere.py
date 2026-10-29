"""PayHere Checkout API; never trust the browser return URL as proof of payment."""
import hashlib
import hmac
from datetime import datetime, timezone

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from . import billing, core
from .db import audit, one, rows


def md5(value):
    return hashlib.md5(value.encode('utf-8')).hexdigest().upper()


def checkout_hash(order_id, amount):
    return md5(settings.PAYMENTS_PAYHERE_MERCHANT_ID+order_id+f'{core.money(amount):.2f}'+'LKR'+md5(settings.PAYMENTS_PAYHERE_MERCHANT_SECRET))


def verify(body):
    if not settings.PAYMENTS_PAYHERE_MERCHANT_SECRET or str(body.get('merchant_id','')) != settings.PAYMENTS_PAYHERE_MERCHANT_ID: return False
    keys=['merchant_id','order_id','payhere_amount','payhere_currency','status_code']
    if any(not isinstance(body.get(k),str) for k in keys): return False
    expected=md5(''.join(body[k] for k in keys)+md5(settings.PAYMENTS_PAYHERE_MERCHANT_SECRET))
    return hmac.compare_digest(expected,str(body.get('md5sig','')).upper())


async def public_view(c,token):
    r=await one(c,'''SELECT pl.code,pl.amount,pl.currency,pl.purpose,pl.status,pl.due_date,pl.expires_at,
        l.first_name,l.last_name,l.learner_no,i.number AS invoice_number,p.name AS programme,e.total_fee,
        COALESCE((SELECT SUM(amount_paid) FROM invoices x WHERE x.enrolment_id=e.id AND x.kind IN ('course','instalment')),0) AS paid_to_date
        FROM payment_links pl JOIN learners l ON l.id=pl.learner_id JOIN invoices i ON i.id=pl.invoice_id
        LEFT JOIN enrolments e ON e.id=i.enrolment_id LEFT JOIN programmes p ON p.code=e.programme_code WHERE pl.token=$1''',token)
    if not r: raise NotFoundError('Payment link not found')
    expiry=r.pop('expires_at')
    if r['status']=='sent' and expiry and expiry<datetime.now(timezone.utc): r['status']='expired'
    return r


async def checkout(c,token):
    r=await one(c,'SELECT * FROM payment_links WHERE token=$1 FOR UPDATE',token)
    if not r: raise NotFoundError('Payment link not found')
    if r['status']!='sent': raise ConflictError('This link is '+r['status'])
    if r['expires_at'] and r['expires_at']<datetime.now(timezone.utc): raise ConflictError('This link has expired')
    if not settings.PAYMENTS_PAYHERE_MERCHANT_ID or not settings.PAYMENTS_PAYHERE_MERCHANT_SECRET: raise ConflictError('PayHere is not configured yet')
    if settings.PAYMENTS_PAYHERE_MODE not in {'sandbox','live'}: raise ConflictError('Invalid PayHere mode')
    demo=await c.fetchval('SELECT demo_data FROM module_state WHERE id=true')
    if demo and settings.PAYMENTS_PAYHERE_MODE=='live': raise ConflictError('Live checkout is disabled while dummy data is loaded')
    inv=await one(c,'SELECT * FROM invoices WHERE id=$1 FOR UPDATE',r['invoice_id'])
    if inv['status']!='open' or inv['amount']-inv['amount_paid']<r['amount']: raise ConflictError('Invoice balance has changed. Request a new link')
    attempt=r['checkout_attempts']+1; order=f"{r['code']}-{attempt}"
    await c.execute('UPDATE payment_links SET checkout_attempts=$1 WHERE id=$2',attempt,r['id'])
    await c.execute('INSERT INTO checkout_orders(order_id,link_id,amount) VALUES($1,$2,$3)',order,r['id'],r['amount'])
    l=await one(c,'SELECT * FROM learners WHERE id=$1',r['learner_id'])
    if not l['email'] or not l['phone']: raise ValidationError('Please ask Accounts to add your email address and phone number')
    base=settings.PAYMENTS_PUBLIC_BASE_URL.rstrip('/')
    if settings.PAYMENTS_PAYHERE_MODE=='live' and not base.startswith('https://'): raise ConflictError('Live checkout requires a public HTTPS address')
    fields=dict(merchant_id=settings.PAYMENTS_PAYHERE_MERCHANT_ID,return_url=f'{base}/pay/{token}?status=return',cancel_url=f'{base}/pay/{token}?status=cancelled',
        notify_url=base+'/api/v1/payments/payhere/notify',order_id=order,items='Inspire College — '+r['purpose'],currency='LKR',amount=f"{r['amount']:.2f}",
        first_name=l['first_name'],last_name=l['last_name'],email=l['email'],phone=l['phone'],address=l['address'] or 'Colombo',city=l['city'] or 'Colombo',country='Sri Lanka',
        custom_1=r['code'],custom_2='',hash=checkout_hash(order,r['amount']))
    return dict(action='https://www.payhere.lk/pay/checkout' if settings.PAYMENTS_PAYHERE_MODE=='live' else 'https://sandbox.payhere.lk/pay/checkout',fields=fields)


async def notify(c,body):
    valid=verify(body)
    try: amount=core.money(body.get('payhere_amount'))
    except ValidationError: amount=None
    try: status=int(body.get('status_code',''))
    except (ValueError,TypeError): status=None
    if status is not None and not -(2**31)<=status<2**31: status=None
    raw={k:str(v)[:1000] for k,v in body.items() if k not in {'card_no','card_expiry','card_holder_name','md5sig'}}

    async def log(outcome,reason):
        await c.execute('''INSERT INTO gateway_events(provider,order_id,payment_id,status_code,amount,currency,method,signature_valid,outcome,raw)
            VALUES('payhere',$1,$2,$3,$4,$5,$6,$7,$8,$9)''',str(body.get('order_id',''))[:200],str(body.get('payment_id',''))[:200] or None,status,amount,
            str(body.get('payhere_currency',''))[:10],str(body.get('method',''))[:30],valid,outcome,dict(raw,reason=reason))
        return dict(ok=outcome!='rejected',reason=reason)

    if not valid: return await log('rejected','invalid signature')
    if status!=2: return await log('ignored','status '+str(status))
    payment_id=body.get('payment_id','').strip()
    if not payment_id or amount is None or amount<=0: return await log('rejected','missing payment ID or invalid amount')
    if await c.fetchval("SELECT 1 FROM gateway_events WHERE provider='payhere' AND payment_id=$1 AND outcome='posted'",payment_id): return await log('duplicate','duplicate payment')
    order=await one(c,'SELECT * FROM checkout_orders WHERE order_id=$1',body.get('order_id',''))
    if not order: return await log('rejected','unknown checkout order')
    link=await one(c,'SELECT * FROM payment_links WHERE id=$1 FOR UPDATE',order['link_id'])
    if body.get('payhere_currency')!='LKR' or amount!=order['amount'] or amount!=link['amount']: return await log('rejected','amount or currency mismatch')
    if link['status']!='sent': return await log('rejected','link is '+link['status'])
    if link['expires_at'] and link['expires_at']<datetime.now(timezone.utc): return await log('rejected','link expired; review captured funds manually')
    inv=await one(c,'SELECT * FROM invoices WHERE id=$1 FOR UPDATE',link['invoice_id'])
    if inv['status']!='open' or inv['amount']-inv['amount_paid']<amount: return await log('rejected','invoice balance changed; review captured funds manually')
    # A locked-period or ledger error rolls back the receipt savepoint, while
    # preserving the callback event for an accountant to investigate.
    try:
        async with c.transaction():
            r=await billing.receipt(c,dict(invoice_id=link['invoice_id'],amount=amount,method='payhere',gateway_payment_id=payment_id,
                received_date=core.today(),memo='PayHere '+payment_id+' — '+link['code']),'payhere')
            await c.execute("UPDATE payment_links SET status='paid',paid_at=now() WHERE id=$1",link['id'])
    except ConflictError:
        return await log('rejected','accounting period locked; review captured funds manually')
    await log('posted','verified payment')
    l=await one(c,'SELECT * FROM learners WHERE id=$1',link['learner_id'])
    if l['email']:
        await billing.enqueue(c,'email',l['email'],f"Hi {l['first_name']},\nWe received LKR {amount:,.2f} for {link['purpose']} (PayHere {payment_id}).\nThank you, Inspire College Accounts",'Payment received — Inspire College')
    return dict(ok=True,posted=r['journal']['number'])


async def simulate(c,uid,actor):
    if not await c.fetchval('SELECT demo_data FROM module_state WHERE id=true'): raise ConflictError('Simulation is available only with dummy data')
    if settings.PAYMENTS_PAYHERE_MODE!='sandbox': raise ConflictError('Simulation requires sandbox mode')
    link=await one(c,'SELECT * FROM payment_links WHERE id=$1 FOR UPDATE',uid)
    if not link: raise NotFoundError('Payment link not found')
    if link['status']!='sent': raise ConflictError('Link is '+link['status'])
    r=await billing.receipt(c,dict(invoice_id=link['invoice_id'],amount=link['amount'],method='payhere',gateway_payment_id='demo:'+link['code'],memo='DUMMY payment '+link['code']),actor)
    await c.execute("UPDATE payment_links SET status='paid',paid_at=now() WHERE id=$1",uid)
    await audit(c,actor,'simulate','payment_link',uid)
    return dict(posted=r['journal']['number'])
