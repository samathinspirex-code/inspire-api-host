import unittest
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from unittest.mock import AsyncMock,patch
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
import httpx
from app.core.errors import APIError,api_error_handler,request_validation_handler
from app.modules.payments import payhere
from app.modules.payments.router import public_router

class PublicPaymentSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_page_denies_framing_and_limits_form_destinations(self):
        app=FastAPI();app.include_router(public_router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            result=await client.get('/pay/token')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.headers['x-frame-options'],'DENY')
            policy=result.headers['content-security-policy']
            self.assertIn("frame-ancestors 'none'",policy)
            self.assertIn('form-action https://www.payhere.lk https://sandbox.payhere.lk',policy)
            self.assertIn("base-uri 'none'",policy)

    async def test_valid_callback_continues_to_verified_processing(self):
        app=FastAPI();app.include_router(public_router)
        connection=object()
        @asynccontextmanager
        async def transaction(write=False):
            self.assertTrue(write)
            yield connection
        with patch('app.modules.payments.router.transaction',transaction),patch.object(payhere,'verify',return_value=True),patch.object(payhere,'notify',AsyncMock(return_value={'ok':True})) as notify:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                result=await client.post('/api/v1/payments/payhere/notify',data=dict(status_code='2',md5sig='test'))
                self.assertEqual(result.status_code,200)
                notify.assert_awaited_once_with(connection,dict(status_code='2',md5sig='test'))

    async def test_payment_json_and_checkout_responses_are_not_cacheable(self):
        app=FastAPI();app.include_router(public_router)
        @asynccontextmanager
        async def transaction(write=False): yield None
        with patch('app.modules.payments.router.transaction',transaction),patch.object(payhere,'public_view',AsyncMock(return_value={'code':'PL-1','status':'sent'})),patch.object(payhere,'checkout',AsyncMock(return_value={'fields':{'currency':'LKR'}})):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                for method,path in [('GET','/api/v1/payments/public/pay/token'),('POST','/api/v1/payments/public/pay/token/checkout')]:
                    result=await client.request(method,path)
                    self.assertEqual(result.status_code,200)
                    self.assertEqual(result.headers['cache-control'],'no-store')
                    self.assertEqual(result.headers['referrer-policy'],'no-referrer')
                    self.assertEqual(result.headers['x-content-type-options'],'nosniff')

    async def test_inactive_links_do_not_return_learner_or_financial_details(self):
        for status in ['paid','cancelled','expired']:
            row=dict(code='PL-1',status=status,expires_at=None,first_name='Private',last_name='Learner',amount=100,total_fee=12000)
            with patch.object(payhere,'one',AsyncMock(return_value=row)):
                self.assertEqual(await payhere.public_view(None,'token'),dict(code='PL-1',status=status))
        row=dict(code='PL-1',status='sent',expires_at=datetime.now(timezone.utc)-timedelta(seconds=1),first_name='Private',amount=100)
        with patch.object(payhere,'one',AsyncMock(return_value=row)):
            self.assertEqual(await payhere.public_view(None,'token'),dict(code='PL-1',status='expired'))

    async def test_forged_callback_never_enters_database_transaction(self):
        app=FastAPI();app.include_router(public_router)
        app.add_exception_handler(APIError,api_error_handler)
        app.add_exception_handler(RequestValidationError,request_validation_handler)
        with patch('app.modules.payments.router.transaction') as transaction,patch.object(payhere,'verify',return_value=False):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                result=await client.post('/api/v1/payments/payhere/notify',data=dict(merchant_id='invalid',md5sig='invalid',status_code='2'))
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.text,'OK')
                transaction.assert_not_called()

    async def test_duplicate_callback_fields_rejected_before_database(self):
        app=FastAPI();app.include_router(public_router);app.add_exception_handler(APIError,api_error_handler)
        with patch('app.modules.payments.router.transaction') as transaction:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                result=await client.post('/api/v1/payments/payhere/notify',content='status_code=2&status_code=-1',headers={'Content-Type':'application/x-www-form-urlencoded'})
                self.assertEqual(result.status_code,400)
                transaction.assert_not_called()
