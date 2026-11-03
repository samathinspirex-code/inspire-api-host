import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.payments import billing,jobs


class ReceiptEmailTests(unittest.IsolatedAsyncioTestCase):
    async def receipt(self,method,amount=40,email='learner@example.com',duplicate=False):
        invoice=dict(id='invoice',number='INV-1',learner_id='learner',enrolment_id=None,status='open',amount=Decimal('100'),amount_paid=Decimal('0'))
        c=SimpleNamespace(execute=AsyncMock())
        journal=dict(id=1,number='JE-1',**({'duplicate':True} if duplicate else {}))
        one=AsyncMock(side_effect=[invoice,dict(id='payment'),dict(first_name='QA learner',email=email)])
        with patch.object(billing,'one',one),patch.object(billing.ledger,'post',AsyncMock(return_value=journal)),patch.object(billing,'enqueue',AsyncMock()) as enqueue:
            result=await billing.receipt(c,dict(invoice_id='invoice',amount=amount,method=method,received_date='2026-11-03'),'test')
        return result,enqueue

    async def test_partial_manual_receipt_queues_actual_amount_and_remaining_balance(self):
        for method in ['cash','bank_transfer']:
            _,queue=await self.receipt(method)
            queue.assert_awaited_once()
            args=queue.await_args.args
            self.assertEqual(args[1:3],('email','learner@example.com'))
            self.assertIn('We received LKR 40.00',args[3])
            self.assertIn('Remaining on this invoice: LKR 60.00',args[3])
            self.assertIn('Receipt reference: JE-1',args[3])

    async def test_excess_receipt_distinguishes_invoice_application_from_advance(self):
        _,queue=await self.receipt('cash',150)
        body=queue.await_args.args[3]
        self.assertIn('Applied to this invoice: LKR 100.00',body)
        self.assertIn('Remaining on this invoice: LKR 0.00',body)
        self.assertIn('Learner advance credited: LKR 50.00',body)

    async def test_no_address_no_duplicate_and_no_second_payhere_acknowledgement(self):
        for method,email,duplicate in [('cash',None,False),('cash','learner@example.com',True),('payhere','learner@example.com',False)]:
            _,queue=await self.receipt(method,email=email,duplicate=duplicate)
            queue.assert_not_awaited()


class DeliveryWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_delivery_does_not_open_database_or_contact_provider(self):
        with patch.object(jobs.settings,'PAYMENTS_MESSAGES_ENABLED',False),patch.object(jobs,'connect',AsyncMock()) as connect,patch.object(jobs,'deliver',AsyncMock()) as deliver:
            self.assertIn('disabled',(await jobs.flush())['skipped'])
            connect.assert_not_awaited();deliver.assert_not_awaited()

    async def test_accepted_message_marked_sent_and_failure_retried_with_limit(self):
        messages=[dict(id=1,channel='email',attempts=0),dict(id=2,channel='email',attempts=4)]
        c=SimpleNamespace(fetchval=AsyncMock(side_effect=[True,False]),fetch=AsyncMock(return_value=messages),execute=AsyncMock(),close=AsyncMock())
        with patch.object(jobs.settings,'PAYMENTS_MESSAGES_ENABLED',True),patch.object(jobs,'connect',AsyncMock(return_value=c)),patch.object(jobs,'codecs',AsyncMock()),patch.object(jobs,'deliver',AsyncMock(side_effect=[None,RuntimeError('Email delivery failed')])) as deliver:
            self.assertEqual(await jobs.flush(),dict(sent=1,failed=1))
            self.assertEqual(deliver.await_count,2)
        sent,failed=c.execute.await_args_list
        self.assertIn("status='sent'",sent.args[0])
        self.assertEqual(sent.args[1],1)
        self.assertIn("attempts+1>=5 THEN 'failed'",failed.args[0])
        self.assertEqual(failed.args[1:],('Email delivery failed',2))
        c.close.assert_awaited_once()
