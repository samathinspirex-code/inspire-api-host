import unittest
from unittest.mock import AsyncMock, patch
from app.modules.payments import billing


class LearnerBalanceTests(unittest.IsolatedAsyncioTestCase):
    async def summary(self, invoices, enrolments=None):
        data=dict(learners=[dict(id='learner',learner_no='S1',first_name='Test',last_name='Learner')],
                  enrolments=enrolments or [],invoices=invoices,instalments=[])
        with patch.object(billing,'one',new=AsyncMock(return_value=data)):
            return (await billing.learners(None,'2026-11-03'))[0]

    def invoice(self,kind='other',amount=100,paid=0,enrolment=None,status='open'):
        return dict(learner_id='learner',enrolment_id=enrolment,kind=kind,amount=amount,amount_paid=paid,status=status,due_date='2026-11-02')

    def plan(self,status='active'):
        return [dict(id='plan',learner_id='learner',status=status,total_fee=1200,programme='Test',instalments=3,counsellor=None)]

    async def test_standalone_unpaid_invoice_is_collectible_and_overdue(self):
        r=await self.summary([self.invoice(paid=40)])
        self.assertEqual(r['balance'],60)
        self.assertEqual(r['status'],'overdue')
        self.assertEqual(r['next_due'],'2026-11-02')
        self.assertEqual(r['next_amount'],60)

    async def test_paid_tuition_does_not_hide_unpaid_registration(self):
        r=await self.summary([self.invoice('instalment',1200,1200,'plan','paid'),self.invoice('registration',100,0,'plan')],self.plan())
        self.assertEqual(r['paid'],1200)
        self.assertEqual(r['balance'],100)
        self.assertEqual(r['status'],'overdue')

    async def test_plan_tuition_is_not_counted_twice_and_extra_fees_are_added(self):
        r=await self.summary([self.invoice('instalment',400,100,'plan'),self.invoice('registration',100,40,'plan')],self.plan())
        self.assertEqual(r['balance'],1160)
        self.assertEqual(r['open_invoiced'],360)

    async def test_withdrawn_plan_owes_only_remaining_open_invoices(self):
        r=await self.summary([self.invoice('other',100,40)],self.plan('withdrawn'))
        self.assertEqual(r['balance'],60)
        self.assertEqual(r['status'],'withdrawn')
