import hashlib
import json
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.errors import ForbiddenError, ValidationError
from app.modules.payments import core, jobs, payhere
from app.modules.payments.router import admin, clear_sample_data, finance, report_csv
from app.modules.payments.settings_validation import validate


class AccountingTests(unittest.TestCase):
    def test_sample_ledger_matches_reference_totals_and_results(self):
        d=json.loads((Path(__file__).parents[1]/'app/modules/payments/assets/demo-snapshot.json').read_text())
        totals={}
        for j in d['journals']:
            lines=[dict(account_code=l[0],debit=l[1],credit=l[2]) for l in j[5]]
            core.validate_journal(dict(entry_date=j[1],memo=j[2],lines=lines),d['accounts'])
            for l in lines:
                t=totals.setdefault(l['account_code'],dict(debit=core.ZERO,credit=core.ZERO))
                t['debit']+=core.money(l['debit']); t['credit']+=core.money(l['credit'])
        tb=core.trial_balance(d['accounts'],totals)
        self.assertEqual(tb['totals'],dict(debit=Decimal('30173099.98'),credit=Decimal('30173099.98')))
        figures=core.profit_loss(d['accounts'],totals)['figures']
        self.assertEqual(figures['revenue'],Decimal('11466000.00'))
        self.assertEqual(figures['net_result'],Decimal('-6181534.98'))

    def test_rounding_and_final_instalment_preserve_total(self):
        self.assertEqual(core.money('1.005'),Decimal('1.01'))
        parts=core.split_instalments('100',3)
        self.assertEqual(parts,[Decimal('33.33'),Decimal('33.33'),Decimal('33.34')])
        self.assertEqual(core.earned_to_date('100',3,'2026-01-31','2026-03-31'),Decimal('100'))
        self.assertEqual(core.add_months('2026-01-31',1),'2026-02-28')
        for bad in ['NaN','Infinity','-Infinity',True,'100000000001']:
            with self.assertRaises(ValidationError): core.money(bad)

    def test_invalid_journals_rejected_before_posting(self):
        accounts=[dict(code='1010',is_active=True),dict(code='1020',is_active=False)]
        for lines in [[dict(account_code='1010',debit=10),dict(account_code='1010',credit=9)],
                      [dict(account_code='1010',debit=-10),dict(account_code='1010',credit=-10)],
                      [dict(account_code='1010',debit=10),dict(account_code='1020',credit=10)]]:
            with self.assertRaises(ValidationError): core.validate_journal(dict(entry_date='2026-10-28',memo='Test',lines=lines),accounts)

    def test_contract_netting_keeps_balance_sheet_balanced(self):
        accs=[dict(code='1100',name='AR',type='asset',grp='CA'),dict(code='1120',name='Accrued',type='asset',grp='CA'),
              dict(code='2100',name='Deferred',type='liability',grp='CL'),dict(code='4010',name='Tuition',type='income',grp='REV')]
        totals={'1100':dict(debit=100,credit=0),'2100':dict(debit=60,credit=100),'4010':dict(debit=0,credit=60)}
        r=core.balance_sheet(accs,totals,dict(asset=Decimal('20'),liability=Decimal('60')))
        self.assertTrue(r['check']['ok']); self.assertEqual(r['figures']['total_assets'],120)

    def test_csv_parser_handles_quoted_amounts_and_duplicate_input_shape(self):
        data=core.parse_bank_csv('date,description,amount,reference\n28/10/2026,"Bank, transfer","1,234.50",REF\n')
        self.assertEqual(data[0]['amount'],Decimal('1234.50'))
        self.assertEqual(data[0]['description'],'Bank, transfer')
        with self.assertRaises(ValidationError): core.parse_bank_csv('2026-02-30,x,100,REF')

    def test_csv_export_neutralises_formula_injection(self):
        out=report_csv(dict(title='Ageing',buckets=[],rows=[dict(ref='=IMPORTXML(1)',name='+CMD',days_overdue=2,bucket_label='1–30',amount=Decimal('3.00'))]))
        self.assertIn("'=IMPORTXML",out); self.assertIn("'+CMD",out)

    def test_settings_validate_rates_and_calendar(self):
        with self.assertRaises(ValidationError): validate('fy_start_month',13)
        with self.assertRaises(ValidationError): validate('ecl_rates',dict(current=2,d30=0,d60=0,d90=0,d90p=0))
        self.assertEqual(validate('registration_fee',5000),5000)


class PaymentSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_dummy_data_blocks_delivery_even_when_enabled(self):
        c=SimpleNamespace(fetchval=AsyncMock(side_effect=[True,True]),close=AsyncMock())
        with patch.object(jobs.settings,'PAYMENTS_MESSAGES_ENABLED',True),patch.object(jobs,'connect',AsyncMock(return_value=c)),patch.object(jobs,'codecs',AsyncMock()),patch.object(jobs,'deliver',AsyncMock()) as deliver:
            self.assertIn('Dummy data',(await jobs.flush())['skipped'])
            deliver.assert_not_awaited()
    async def test_explicit_finance_access_required_even_for_admin(self):
        for grants in [[],['ADMIN'],['CMS']]:
            with self.assertRaises(ForbiddenError): await finance(SimpleNamespace(access=grants))
        await finance(SimpleNamespace(access=['FINANCE']))
        with self.assertRaises(ForbiddenError): await admin(SimpleNamespace(access=['FINANCE']))
        await admin(SimpleNamespace(access=['FINANCE','ADMIN']))

    async def test_clearing_sample_data_requires_exact_confirmation(self):
        user=SimpleNamespace(email='finance@example.com')
        with self.assertRaises(ValidationError):
            await clear_sample_data({'confirmation':'clear'},user,None)
        with patch('app.modules.payments.router.setup.clear_sample_ledger',AsyncMock(return_value={'ok':True})) as clear:
            self.assertEqual(await clear_sample_data({'confirmation':'CLEAR SAMPLE DATA'},user,None),{'ok':True})
            clear.assert_awaited_once_with(None,user.email)

    async def test_payhere_signature_uses_exact_formatted_amount_and_merchant(self):
        with patch.object(payhere.settings,'PAYMENTS_PAYHERE_MERCHANT_ID','1211149'),patch.object(payhere.settings,'PAYMENTS_PAYHERE_MERCHANT_SECRET','secret'):
            md5=lambda s:hashlib.md5(s.encode()).hexdigest().upper()
            self.assertEqual(payhere.checkout_hash('PL-1-1',35000),md5('1211149PL-1-135000.00LKR'+md5('secret')))
            b=dict(merchant_id='1211149',order_id='PL-1-1',payhere_amount='35000.00',payhere_currency='LKR',status_code='2')
            b['md5sig']=md5('1211149PL-1-135000.00LKR2'+md5('secret'))
            self.assertTrue(payhere.verify(b))
            self.assertFalse(payhere.verify(dict(b,payhere_amount='1.00')))
            self.assertFalse(payhere.verify(dict(b,merchant_id='different')))


if __name__=='__main__': unittest.main()
