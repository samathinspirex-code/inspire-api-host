"""Use only a disposable local database: PAYMENTS_TEST_DATABASE_URL=postgresql://..."""
import os
import unittest
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.core.errors import APIError, api_error_handler, request_validation_handler
from app.modules.auth.dependencies import get_current_user
from app.modules.payments import billing, closing, core, db, jobs, ledger, payhere, reports, setup
from app.modules.payments.router import database, public_router, router

URL=os.environ.get('PAYMENTS_TEST_DATABASE_URL')


@unittest.skipUnless(URL,'Set PAYMENTS_TEST_DATABASE_URL to a disposable local PostgreSQL database')
class PaymentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_workspace_and_ledger_guarantees(self):
        if urlparse(URL).hostname not in {'localhost','127.0.0.1','::1'}:
            self.fail('Integration tests require an explicitly configured disposable local database')
        c=await asyncpg.connect(URL,ssl=False,statement_cache_size=0,timeout=10,command_timeout=20)
        try:
            await db.codecs(c)
            await setup.migrate(c); await setup.migrate(c)
            await setup.seed_demo(c)
            self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM learners'),134)
            self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM journals'),939)
            self.assertFalse(await setup.seed_demo(c))
            for kind in ['trial-balance','balance-sheet','cash-flow']:
                self.assertTrue((await reports.report(c,kind,{'to':'2026-10-19','from':'2026-04-01'}))['check']['ok'])
            # The whole workflow is rolled back, leaving the sample database unchanged.
            outer=c.transaction(); await outer.start()
            try:
                async with c.transaction():
                    with self.assertRaises(asyncpg.CheckViolationError):
                        async with c.transaction():
                            await c.execute("INSERT INTO journals(number,entry_date,memo,source,created_by) VALUES('TEST-LOCK','2026-05-01','Locked','manual','test')")
                with self.assertRaises(asyncpg.CheckViolationError):
                    async with c.transaction():
                        await c.execute("INSERT INTO journals(number,entry_date,memo,source,created_by) VALUES('TEST-EMPTY','2026-10-28','Empty','manual','test')")
                        await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
                with self.assertRaises(asyncpg.InsufficientPrivilegeError):
                    async with c.transaction():
                        await c.execute("INSERT INTO journal_lines(journal_id,account_code,debit,credit) VALUES((SELECT MIN(id) FROM journals),'1020',1,0)")
                with self.assertRaises(asyncpg.InsufficientPrivilegeError):
                    async with c.transaction(): await c.execute("UPDATE journals SET memo='Changed' WHERE id=(SELECT MIN(id) FROM journals)")

                app=FastAPI()
                app.add_exception_handler(APIError,api_error_handler)
                app.add_exception_handler(RequestValidationError,request_validation_handler)
                app.include_router(router); app.include_router(public_router)
                user=SimpleNamespace(user_id=7,email='test@example.com',access=['FINANCE','ADMIN'])
                async def actor(): return user
                async def database_override():
                    async with c.transaction(): yield c
                app.dependency_overrides[get_current_user]=actor
                app.dependency_overrides[database]=database_override
                @asynccontextmanager
                async def local_transaction(write=False):
                    async with c.transaction(): yield c
                transport=httpx.ASGITransport(app=app)
                with patch('app.modules.payments.router.transaction',local_transaction), \
                     patch.object(payhere.settings,'PAYMENTS_PAYHERE_MERCHANT_ID','1211149'), \
                     patch.object(payhere.settings,'PAYMENTS_PAYHERE_MERCHANT_SECRET','test-secret'), \
                     patch.object(payhere.settings,'PAYMENTS_PAYHERE_MODE','sandbox'):
                    async with httpx.AsyncClient(transport=transport,base_url='http://test') as client:
                        base='/api/v1/cms/payments'; seq=0
                        async def post(path,body):
                            nonlocal seq
                            seq+=1
                            r=await client.post(base+path,json=body,headers={'Idempotency-Key':f'integration-{seq}'})
                            self.assertLess(r.status_code,300,r.text)
                            return r.json()
                        for path in ['/me','/dashboard','/accounts','/programmes','/learners','/journals','/payment-links','/banking/1020/summary','/banking/1020/lines','/suppliers','/bills','/settings','/fixed-assets','/posting-rules','/close/2026-10','/gateway-events','/messages']:
                            r=await client.get(base+path); self.assertEqual(r.status_code,200,(path,r.text))
                        for kind in ['profit-loss','balance-sheet','cash-flow','trial-balance','ar-ageing','ap-ageing','deferred-revenue','general-ledger']:
                            r=await client.get(base+'/reports/'+kind); self.assertEqual(r.status_code,200,(kind,r.text))
                        bad=await client.post(base+'/learner-enrolments',json=dict(first_name='Atomic',last_name='Failure',enrolment=dict(programme_code='NOT-REAL',start_date='2026-10-28',total_fee=1000)),headers={'Idempotency-Key':'failed-enrolment'})
                        self.assertEqual(bad.status_code,404)
                        self.assertFalse(await c.fetchval("SELECT 1 FROM learners WHERE first_name='Atomic'"))
                        record=await post('/learner-enrolments',dict(first_name='Workflow',last_name='Test',email='workflow@example.com',phone='+94770000000',enrolment=dict(programme_code='HND-BUS',start_date='2026-10-28',total_fee=10000,instalments=2,registration_fee=0)))
                        learner=record['learner']; link=await post('/payment-links',dict(learner_id=learner['id'],kind='instalment',channel='email'))
                        view=await client.get('/api/v1/payments/public/pay/'+link['token']); self.assertEqual(view.status_code,200)
                        checkout=await client.post('/api/v1/payments/public/pay/'+link['token']+'/checkout'); self.assertEqual(checkout.status_code,200,checkout.text)
                        fields=checkout.json()['fields']; self.assertNotIn('test-secret',checkout.text)
                        body=dict(merchant_id='1211149',order_id=fields['order_id'],payment_id='integration-payment',payhere_amount=fields['amount'],payhere_currency='LKR',status_code='2',method='VISA')
                        body['md5sig']=payhere.md5(''.join(body[k] for k in ['merchant_id','order_id','payhere_amount','payhere_currency','status_code'])+payhere.md5('test-secret'))
                        n=await c.fetchval('SELECT COUNT(*) FROM journals')
                        rejected=await payhere.notify(c,dict(body,payhere_amount='1.00')); self.assertFalse(rejected['ok'])
                        self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM journals'),n)
                        r=await client.post('/api/v1/payments/payhere/notify',data=body); self.assertEqual(r.status_code,200,r.text)
                        n=await c.fetchval('SELECT COUNT(*) FROM journals')
                        await client.post('/api/v1/payments/payhere/notify',data=body)
                        self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM journals'),n)
                        self.assertEqual(await c.fetchval("SELECT COUNT(*) FROM gateway_events WHERE payment_id='integration-payment' AND outcome='posted'"),1)
                        paid=await c.fetchval('SELECT status FROM payment_links WHERE id=$1',link['id']); self.assertEqual(paid,'paid')
                        second=await post('/payment-links',dict(learner_id=learner['id'],kind='instalment',channel='email'))
                        attempt=(await client.post('/api/v1/payments/public/pay/'+second['token']+'/checkout')).json()['fields']
                        data=dict(invoice_id=second['invoice_id'],amount=1000,method='bank_transfer')
                        key={'Idempotency-Key':'same-receipt'}
                        r=await client.post(base+'/receipts',json=data,headers=key); self.assertEqual(r.status_code,201,r.text)
                        n=await c.fetchval('SELECT COUNT(*) FROM journals')
                        again=await client.post(base+'/receipts',json=data,headers=key); self.assertEqual(again.status_code,201,again.text)
                        self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM journals'),n)
                        self.assertEqual(r.json()['journal']['number'],again.json()['journal']['number'])
                        mismatch=await client.post(base+'/receipts',json=dict(data,amount=2000),headers=key); self.assertEqual(mismatch.status_code,400)
                        # Cancellation and expiry must never turn verified callbacks into receipts.
                        cancelled=dict(body,order_id=attempt['order_id'],payment_id='cancelled-payment',payhere_amount=attempt['amount'])
                        cancelled['md5sig']=payhere.md5(''.join(cancelled[k] for k in ['merchant_id','order_id','payhere_amount','payhere_currency','status_code'])+payhere.md5('test-secret'))
                        await post('/payment-links/'+second['id']+'/cancel',{})
                        self.assertFalse((await payhere.notify(c,cancelled))['ok'])
                        self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM journals'),n)
                        expired=await post('/payment-links',dict(learner_id=learner['id'],kind='other',amount=50,description='Expiry test',channel='email'))
                        exp_fields=(await client.post('/api/v1/payments/public/pay/'+expired['token']+'/checkout')).json()['fields']
                        exp_body=dict(body,order_id=exp_fields['order_id'],payment_id='expired-payment',payhere_amount=exp_fields['amount'])
                        exp_body['md5sig']=payhere.md5(''.join(exp_body[k] for k in ['merchant_id','order_id','payhere_amount','payhere_currency','status_code'])+payhere.md5('test-secret'))
                        await c.execute("UPDATE payment_links SET expires_at=now()-interval '1 day' WHERE id=$1",expired['id'])
                        self.assertFalse((await payhere.notify(c,exp_body))['ok'])
                        blocked=await client.post('/api/v1/payments/public/pay/'+expired['token']+'/checkout'); self.assertEqual(blocked.status_code,409)
                        supplier=await post('/suppliers',dict(name='Workflow supplier',kind='vendor'))
                        bill=await post('/bills',dict(supplier_id=supplier['id'],bill_date='2026-10-28',expense_account='6090',description='Workflow costs',amount=100))
                        await post('/bills/'+bill['id']+'/pay',{})
                        raw='date,description,amount,reference\n2026-10-28,Workflow bank charge,-12.50,TEST-REF\n'
                        r=await client.post(base+'/banking/1020/import',content=raw,headers={'Content-Type':'text/csv'}); self.assertEqual(r.status_code,201,r.text)
                        again=await client.post(base+'/banking/1020/import',content=raw,headers={'Content-Type':'text/csv'}); self.assertEqual(again.json()['duplicates'],1)
                        bank_id=await c.fetchval("SELECT id FROM bank_statement_lines WHERE external_ref='TEST-REF'")
                        await post('/banking/lines/'+bank_id+'/post',dict(account_code='6065'))
                        payout='date,description,amount,reference\n2026-10-28,Workflow payout,100,TEST-PAYOUT\n'
                        r=await client.post(base+'/banking/1020/import',content=payout,headers={'Content-Type':'text/csv'}); self.assertEqual(r.status_code,201,r.text)
                        payout_id=await c.fetchval("SELECT id FROM bank_statement_lines WHERE external_ref='TEST-PAYOUT'")
                        clearing=await ledger.balance(c,'1030')
                        await post('/banking/lines/'+payout_id+'/payhere-settlement',dict(fee=3.30))
                        self.assertEqual(await ledger.balance(c,'1030'),clearing-Decimal('103.30'))
                        j=await post('/journals',dict(entry_date='2026-10-28',memo='Workflow journal',lines=[dict(account_code='1020',debit=20),dict(account_code='3010',credit=20)]))
                        await post('/journals/'+str(j['id'])+'/reverse',dict(entry_date='2026-10-28',reason='Workflow reversal'))
                        for kind in ['revenue','depreciation','ecl']:
                            preview=await client.post(base+'/close/2026-10/run/'+kind+'?dry_run=1',json={}); self.assertEqual(preview.status_code,200,preview.text)
                            await post('/close/2026-10/run/'+kind,{})
                            n=await c.fetchval('SELECT COUNT(*) FROM journals')
                            await post('/close/2026-10/run/'+kind,{})
                            self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM journals'),n)
                        for t in closing.TASKS:
                            r=await client.put(base+'/close/2026-10/tasks/'+t['key'],json=dict(done=True)); self.assertEqual(r.status_code,200,r.text)
                        await post('/close/2026-10/lock',{})
                        locked=await client.post(base+'/receipts',json=data,headers={'Idempotency-Key':'locked-receipt'}); self.assertEqual(locked.status_code,409,locked.text)
                        await post('/close/2026-10/unlock',dict(reason='Integration test'))
                        self.assertTrue((await reports.report(c,'trial-balance',{}))['check']['ok'])
                        self.assertTrue((await reports.report(c,'balance-sheet',{}))['check']['ok'])
                        self.assertTrue((await reports.report(c,'cash-flow',{}))['check']['ok'])
                        await post('/close/year-end',dict(fy_end='2027-03-31'))
                        self.assertEqual((await reports.report(c,'profit-loss',{'from':'2026-04-01','to':'2027-03-31'}))['figures']['net_result'],0)
                        # No administrator bypass of the explicit finance grant.
                        user.access=['ADMIN']
                        denied=await client.get(base+'/dashboard'); self.assertEqual(denied.status_code,403)
                        self.assertTrue((await jobs.flush()).get('skipped'))
            finally: await outer.rollback()
            clear_tx=c.transaction(); await clear_tx.start()
            try:
                cleared=await setup.clear_sample_ledger(c,'test@example.com')
                self.assertTrue(cleared['ok'])
                self.assertEqual(cleared['removed']['learners'],134)
                for table in ('learners','journals','invoices','payments','payment_links','bills','fiscal_periods','settings'):
                    self.assertEqual(await c.fetchval(f'SELECT COUNT(*) FROM {table}'),0,table)
                self.assertFalse(await c.fetchval('SELECT demo_data FROM module_state WHERE id=true'))
                self.assertGreater(await c.fetchval('SELECT COUNT(*) FROM accounts'),0)
                self.assertGreater(await c.fetchval('SELECT COUNT(*) FROM programmes'),0)
                self.assertFalse(await setup.seed_demo(c))
            finally: await clear_tx.rollback()
            self.assertEqual(await c.fetchval('SELECT COUNT(*) FROM learners'),134)
        finally:
            # terminate also works with disposable wire-protocol test servers.
            c.terminate()


if __name__=='__main__': unittest.main()
