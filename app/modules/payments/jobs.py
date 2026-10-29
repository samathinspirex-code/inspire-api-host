"""Explicit worker entrypoints; API requests never send messages to providers."""
import html
import json
from datetime import date, timedelta

import httpx

from app.core.config import settings
from app.core.public_form_email import send_public_form_email
from . import billing, core
from .db import audit, codecs, connect, rows, transaction


async def daily(as_of=None):
    end=core.day(as_of or core.today())
    async with transaction(write=True) as c:
        if await c.fetchval('SELECT demo_data FROM module_state WHERE id=true'):
            return dict(skipped='Dummy data loaded; scheduled billing and messaging are disabled')
        s=await billing.settings(c); issued=late=reminders=0
        await c.execute("UPDATE payment_links SET status='expired' WHERE status='sent' AND expires_at<now()")
        horizon=(date.fromisoformat(end)+timedelta(days=s['invoice_lead_days'])).isoformat()
        its=await rows(c,"SELECT i.id FROM instalments i JOIN enrolments e ON e.id=i.enrolment_id WHERE i.status='scheduled' AND e.status='active' AND i.due_date<=$1 ORDER BY i.due_date",horizon)
        for it in its:
            inv=await billing.invoice_instalment(c,it['id'],'scheduler',end)
            l=await c.fetchrow('SELECT * FROM learners WHERE id=$1',inv['learner_id'])
            if l['email'] or l['phone']:
                channel='whatsapp' if l['phone'] and settings.PAYMENTS_WHATSAPP_TOKEN else 'email' if l['email'] else 'sms'
                await billing.create_link(c,dict(invoice_id=inv['id'],channel=channel),'scheduler')
            else: await audit(c,'scheduler','missing_contact','invoice',inv['id'])
            issued+=1
        if s['late_fee']['enabled'] and core.money(s['late_fee']['amount'])>0:
            cutoff=(date.fromisoformat(end)-timedelta(days=s['late_fee']['after_days'])).isoformat()
            overdue=await rows(c,"""SELECT i.* FROM invoices i WHERE i.status='open' AND i.kind IN ('course','instalment') AND i.due_date<$1
                AND NOT EXISTS(SELECT 1 FROM invoices x WHERE x.kind='late_fee' AND x.description='Late payment fee on '||i.number)""",cutoff)
            for inv in overdue:
                await billing.invoice(c,dict(learner_id=inv['learner_id'],enrolment_id=inv['enrolment_id'],kind='late_fee',description='Late payment fee on '+inv['number'],issue_date=end,due_date=end,amount=s['late_fee']['amount']),'scheduler'); late+=1
        for link in await rows(c,"SELECT pl.*,l.email,l.phone,l.first_name FROM payment_links pl JOIN learners l ON l.id=pl.learner_id WHERE pl.status='sent'"):
            d=core.days(end,link['due_date']); r=s['reminders']
            stage=1 if r['before_days'] and d==r['before_days'] else 2 if r['on_due'] and d==0 else 3 if r['after_days'] and d==-r['after_days'] else 0
            if stage and link['reminders_sent']<stage:
                to=link['email'] if link['channel']=='email' else link['phone']
                if to:
                    msg=f"Hi {link['first_name']},\nReminder: {link['purpose']} of LKR {link['amount']:,.2f}, due {link['due_date']}.\n{billing.link_url(link)}"
                    await billing.enqueue(c,link['channel'],to,msg,'Fee reminder — Inspire College',link['id'],[link['first_name'],link['purpose'],f"LKR {link['amount']:,.2f}",link['due_date'],billing.link_url(link)])
                    await c.execute('UPDATE payment_links SET reminders_sent=$1,last_reminder_at=now() WHERE id=$2',stage,link['id']); reminders+=1
        await audit(c,'scheduler','daily','payments',end,dict(invoices=issued,late_fees=late,reminders=reminders))
        return dict(invoices=issued,late_fees=late,reminders=reminders)


async def deliver(msg):
    if msg['channel']=='email':
        r=await send_public_form_email(msg['subject'] or 'Inspire College Accounts',msg['body'],'<p style="white-space:pre-wrap">'+html.escape(msg['body'])+'</p>',
            custom_id='payments-'+str(msg['id']),to_email=msg['to_addr'],to_name='Inspire learner')
        if not r.sent: raise RuntimeError(r.error or 'Email delivery failed')
        return
    async with httpx.AsyncClient(timeout=20) as client:
        if msg['channel']=='whatsapp':
            if not all([settings.PAYMENTS_WHATSAPP_TOKEN,settings.PAYMENTS_WHATSAPP_PHONE_ID,settings.PAYMENTS_WHATSAPP_API_VERSION]): raise RuntimeError('WhatsApp is not configured')
            params=json.loads(msg['template'] or '[]')
            r=await client.post(f'https://graph.facebook.com/{settings.PAYMENTS_WHATSAPP_API_VERSION}/{settings.PAYMENTS_WHATSAPP_PHONE_ID}/messages',
                headers={'Authorization':'Bearer '+settings.PAYMENTS_WHATSAPP_TOKEN},json=dict(messaging_product='whatsapp',to=''.join(x for x in msg['to_addr'] if x.isdigit()),type='template',
                template=dict(name=settings.PAYMENTS_WHATSAPP_TEMPLATE,language=dict(code=settings.PAYMENTS_WHATSAPP_LANGUAGE),components=[dict(type='body',parameters=[dict(type='text',text=str(x)) for x in params])])))
        else:
            if not settings.PAYMENTS_SMS_URL.startswith('https://'): raise RuntimeError('An HTTPS SMS adapter URL is required')
            r=await client.post(settings.PAYMENTS_SMS_URL,headers={'Authorization':'Bearer '+settings.PAYMENTS_SMS_TOKEN},json=dict(sender_id=settings.PAYMENTS_SMS_SENDER,to=msg['to_addr'],message=msg['body']))
        if not r.is_success: raise RuntimeError('Message provider returned status '+str(r.status_code))


async def flush():
    if not settings.PAYMENTS_MESSAGES_ENABLED: return dict(skipped='Messages disabled; queued messages preserved')
    c=await connect()
    try:
        await codecs(c)
        if not await c.fetchval('SELECT pg_try_advisory_lock(847239611)'): return dict(skipped='Another delivery worker is running')
        if await c.fetchval('SELECT demo_data FROM module_state WHERE id=true'): return dict(skipped='Dummy data loaded; no messages sent')
        data=await rows(c,"SELECT * FROM message_outbox WHERE status='queued' AND attempts<5 ORDER BY id LIMIT 25")
        sent=failed=0
        for msg in data:
            try:
                await deliver(msg)
                await c.execute("UPDATE message_outbox SET status='sent',attempts=attempts+1,sent_at=now(),error=NULL WHERE id=$1",msg['id']); sent+=1
            except Exception as e:
                # Provider response bodies and credentials never enter logs.
                reason=str(e) if isinstance(e,RuntimeError) else type(e).__name__
                await c.execute("UPDATE message_outbox SET attempts=attempts+1,error=$1,status=CASE WHEN attempts+1>=5 THEN 'failed' ELSE 'queued' END WHERE id=$2",reason[:200],msg['id']); failed+=1
        return dict(sent=sent,failed=failed)
    finally:
        await c.close()
