"""Website checkout end to end on a disposable local PostgreSQL: PAYMENTS_TEST_DATABASE_URL=postgresql://user:pass@localhost/db

Covers: migration twice, order outside the ledger, signed PayHere notify -> learner/enrolment/invoice/receipt/journals,
schedule from the payment date (month-end), duplicate notify, daily job issuing a website link, partial and full slip
approval, set plan for a reservation, Year 2, and the Learners & fees balance across two enrolments.
"""
import hashlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import asyncpg

from app.modules.payments import billing, core, db, jobs, payhere, setup
from app.modules.website import checkout

URL = os.environ.get("PAYMENTS_TEST_DATABASE_URL")
SQL = Path(__file__).resolve().parents[1] / "app/modules/website/sql/001_website.sql"


@unittest.skipUnless(URL, "Set PAYMENTS_TEST_DATABASE_URL to a disposable local PostgreSQL database")
class WebsiteCheckoutIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if urlparse(URL).hostname not in {"localhost", "127.0.0.1", "::1"}:
            self.fail("Integration tests require a disposable local database")
        self.c = await asyncpg.connect(URL, ssl=False, statement_cache_size=0)
        await db.codecs(self.c)
        await setup.migrate(self.c)
        await self.c.execute("CREATE TABLE IF NOT EXISTS public.academic_courses (course_id serial PRIMARY KEY, slug text)")
        for _ in range(2):  # safe to rerun
            await self.c.execute(SQL.read_text(encoding="utf-8"))
        if await self.c.fetchval("SELECT COUNT(*) FROM learners"):
            await self.c.close()
            self.skipTest("Needs an empty ledger: use a separate database from test_payments_integration (it loads sample data)")
        self.tx = self.c.transaction()
        await self.tx.start()
        self.patches = [patch.object(payhere.settings, "PAYMENTS_PAYHERE_MERCHANT_ID", "1211149"),
                        patch.object(payhere.settings, "PAYMENTS_PAYHERE_MERCHANT_SECRET", "test-secret"),
                        patch.object(payhere.settings, "PAYMENTS_PAYHERE_MODE", "sandbox"),
                        patch.object(payhere.settings, "WEB_CHECKOUT_ENABLED", True),
                        patch.object(payhere.settings, "WEB_API_ENABLED", True),
                        patch.object(payhere.settings, "PAYMENTS_MAIL_TEST_TO", ""),
                        patch.object(payhere.settings, "WEB_SITE_URL", "https://web.test"),
                        patch.object(payhere.settings, "PAYMENTS_PUBLIC_BASE_URL", "https://api.test")]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.tx.rollback()
        await self.c.close()

    async def order(self, code="monthly", amount=10000, lines=None, method="payhere", name="ZZ TEST Student", email="zz@test.example"):
        terms = {"code": code, "today": amount, "instalments": 9 if code == "monthly" else 0, "instalment_amount": 9999 if code == "monthly" else 0,
                 "label": code}
        course = {"course_id": 1, "title": "HND Software Engineering", "plan_type": "hnd"}
        lines = lines or [dict(course_id=1, title=course["title"], plan_type="hnd", kind="main", amount=amount, programme_code="HND-COM", auto=True)]
        return await checkout.create_order(self.c, course=course, terms=terms, plan_label=code, lines=lines, method=method, name=name,
                                           email=email, phone="0771234567", crm_lead_id=None, extra={})

    async def notify(self, order_row, payment_id="PH1", amount=None):
        form = await checkout.checkout(self.c, order_row["token"])
        f = form["fields"]
        amt = f["amount"]
        md5 = lambda v: hashlib.md5(v.encode()).hexdigest().upper()
        body = dict(merchant_id="1211149", order_id=f["order_id"], payhere_amount=amount or amt, payhere_currency="LKR", status_code="2", payment_id=payment_id)
        body["md5sig"] = md5(body["merchant_id"] + body["order_id"] + body["payhere_amount"] + "LKR" + "2" + md5("test-secret"))
        self.last_body = body
        return await payhere.notify(self.c, body)

    async def test_card_payment_creates_ledger_records_and_schedule(self):
        with patch.object(core, "today", return_value="2027-01-31"):
            o = await self.order()
            self.assertEqual(await self.c.fetchval("SELECT COUNT(*) FROM learners"), 0)   # nothing in the ledger yet
            r = await self.notify(o)
            self.assertTrue(r["ok"], r)
            dup = await payhere.notify(self.c, dict(self.last_body))       # PayHere retries the same callback
            self.assertEqual(dup["reason"], "duplicate payment")
            self.assertEqual(await self.c.fetchval("SELECT COUNT(*) FROM payments"), 1)
        order = await self.c.fetchrow("SELECT * FROM web_orders WHERE id=$1", o["id"])
        self.assertEqual(order["status"], "paid")
        reg = await self.c.fetchrow("SELECT * FROM invoices WHERE kind='registration'")
        self.assertEqual((reg["status"], reg["amount_paid"]), ("paid", 10000))
        dues = [r["due_date"] for r in await self.c.fetch("SELECT due_date FROM instalments ORDER BY seq")]
        self.assertEqual(dues[:3], ["2027-02-28", "2027-03-31", "2027-04-30"])
        self.assertEqual(len(dues), 9)
        self.assertEqual(await self.c.fetchval("SELECT COUNT(*) FROM payments"), 1)
        # Daily job issues month 1 five days ahead, with a website link and branded email.
        with patch.object(core, "today", return_value="2027-02-23"), patch.object(jobs, "transaction") as tx:
            tx.return_value.__aenter__.return_value = self.c
            tx.return_value.__aexit__.return_value = False
            await jobs.daily("2027-02-23")
        link = await self.c.fetchrow("SELECT * FROM payment_links")
        self.assertEqual((link["origin"], link["expires_at"]), ("website", None))
        self.assertIn("https://web.test/pay/", billing.link_url(dict(link)))
        msg = await self.c.fetchrow("SELECT * FROM message_outbox WHERE payment_link_id=$1", link["id"])
        self.assertIn("Pay now", msg["html_body"])
        view = await checkout.pay_view(self.c, o["token"])
        self.assertEqual(view["summary"]["instalments"], 9)

    async def test_slips_partial_then_full_and_balance(self):
        with patch.object(core, "today", return_value="2027-03-10"):
            o = await self.order(method="bank_transfer")
            target = await checkout.slip_target(self.c, o["token"])
            s1 = await checkout.add_slip(self.c, target, key="k1", file_name="a.pdf", mime="application/pdf", size=10, amount=4000,
                                         paid_on="2027-03-09", reference="ref", note="")
            await checkout.approve_slip(self.c, s1["id"], "finance@test")
            reg = await self.c.fetchrow("SELECT * FROM invoices WHERE kind='registration'")
            self.assertEqual((reg["status"], reg["amount_paid"]), ("open", 4000))
            self.assertEqual(await self.c.fetchval("SELECT COUNT(*) FROM instalments"), 0)   # schedule waits for full registration
            target = await checkout.slip_target(self.c, o["token"])
            s2 = await checkout.add_slip(self.c, target, key="k2", file_name="b.pdf", mime="application/pdf", size=10, amount=6000,
                                         paid_on="2027-03-10", reference="ref2", note="")
            await checkout.approve_slip(self.c, s2["id"], "finance@test")
            self.assertEqual(await self.c.fetchval("SELECT COUNT(*) FROM instalments"), 9)
            first = await self.c.fetchval("SELECT due_date FROM instalments WHERE seq=1")
            self.assertEqual(first, "2027-04-10")
            learners = await billing.learners(self.c, "2027-03-10")
            self.assertEqual(learners[0]["balance"], 9 * 9999)

    async def test_reservation_set_plan_and_year2(self):
        with patch.object(core, "today", return_value="2027-05-01"):
            o = await self.order(code="reserve", amount=10000)
            await self.notify(o, payment_id="PH9")
            e = await self.c.fetchrow("SELECT enrolment_id FROM web_enrolments")
            self.assertEqual(await self.c.fetchval("SELECT COUNT(*) FROM instalments"), 0)
            await checkout.set_plan(self.c, e["enrolment_id"], plan_code="monthly", instalments=3, amount=20000, first_due="2027-06-15", actor="fin")
            self.assertEqual(await self.c.fetchval("SELECT total_fee FROM enrolments WHERE id=$1", e["enrolment_id"]), 60000)
            y2 = await checkout.start_year2(self.c, e["enrolment_id"], instalments=2, amount=9999, first_due="2028-01-05", actor="fin")
            with self.assertRaises(Exception):
                await checkout.start_year2(self.c, e["enrolment_id"], instalments=2, amount=9999, first_due="2028-01-05", actor="fin")
            learners = await billing.learners(self.c, "2027-05-01")
            self.assertEqual(learners[0]["balance"], 60000 + 2 * 9999)
            self.assertTrue(y2["ok"])

    async def test_addon_course_gets_its_own_enrolment(self):
        lines = [dict(course_id=1, title="AI Mastery", plan_type="cpd", kind="main", amount=45000, programme_code="CPD", auto=True),
                 dict(course_id=2, title="Looker Studio", plan_type="cpd", kind="addon", amount=30000, programme_code="CPD", auto=True)]
        with patch.object(core, "today", return_value="2027-02-01"):
            o = await self.order(code="full", amount=45000, lines=lines)
            r = await self.notify(o, payment_id="PH5")
        self.assertTrue(r["ok"], r)
        self.assertEqual(await self.c.fetchval("SELECT COUNT(*) FROM enrolments"), 2)
        self.assertEqual(await self.c.fetchval("SELECT COUNT(*) FROM invoices WHERE status='paid'"), 2)
        self.assertEqual(sorted(r["gateway_payment_id"] for r in await self.c.fetch("SELECT gateway_payment_id FROM payments")), ["PH5", "PH5:2"])


if __name__ == "__main__":
    unittest.main()
