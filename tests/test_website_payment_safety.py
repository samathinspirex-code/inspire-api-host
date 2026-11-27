"""Website payments: double payments, retries and edge cases, on a disposable local PostgreSQL.

PAYMENTS_TEST_DATABASE_URL=postgresql://user@localhost/db (an empty database; this file never touches a remote host).
Covers: a second card payment on a paid order, slip then card, a slip approved twice, the same slip file twice, the same order
submitted twice, card blocked while a slip waits, bad callbacks (signature, amount, currency, failed statuses), a locked period,
an overpaid slip, the daily job run twice, reminders once per stage, cancelled / expired orders, and two simultaneous callbacks.
"""
import asyncio
import hashlib
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse, urlunparse

import asyncpg

from app.core.errors import APIError, ConflictError, NotFoundError
from app.modules.payments import core, db, jobs, payhere, setup
from app.modules.payments.db import MUTATION_LOCK
from app.modules.website import checkout

URL = os.environ.get("PAYMENTS_TEST_DATABASE_URL")
SQL = Path(__file__).resolve().parents[1] / "app/modules/website/sql/001_website.sql"
md5 = lambda v: hashlib.md5(v.encode()).hexdigest().upper()
PATCHES = dict(PAYMENTS_PAYHERE_MERCHANT_ID="1211149", PAYMENTS_PAYHERE_MERCHANT_SECRET="test-secret", PAYMENTS_PAYHERE_MODE="sandbox",
               WEB_CHECKOUT_ENABLED=True, WEB_API_ENABLED=True, PAYMENTS_MAIL_TEST_TO="", WEB_SITE_URL="https://web.test",
               PAYMENTS_PUBLIC_BASE_URL="https://api.test")


def local_only():
    if urlparse(URL).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise AssertionError("These tests require a disposable local database")


def signed(order_id, amount, payment_id, status="2", currency="LKR", secret="test-secret"):
    body = dict(merchant_id="1211149", order_id=order_id, payhere_amount=amount, payhere_currency=currency, status_code=status, payment_id=payment_id)
    body["md5sig"] = md5(body["merchant_id"] + order_id + amount + currency + status + md5(secret))
    return body


async def prepare(c):
    await db.codecs(c)
    await setup.migrate(c)
    await c.execute("CREATE TABLE IF NOT EXISTS public.academic_courses (course_id serial PRIMARY KEY, slug text)")
    await c.execute(SQL.read_text(encoding="utf-8"))


async def new_order(c, *, code="full", amount=50000, method="payhere", email="zz@test.example", phone="0771234567", lines=None):
    terms = {"code": code, "today": amount, "instalments": 9 if code == "monthly" else 0, "instalment_amount": 9999 if code == "monthly" else 0,
             "label": code}
    course = {"course_id": 1, "title": "ZZ TEST Course", "plan_type": "cpd"}
    lines = lines or [dict(course_id=1, title=course["title"], plan_type="cpd", kind="main", amount=amount, programme_code="CPD", auto=True)]
    return await checkout.create_order(c, course=course, terms=terms, plan_label=code, lines=lines, method=method, name="ZZ TEST Student",
                                       email=email, phone=phone, crm_lead_id=None, extra={})


async def card_attempt(c, order):
    form = await checkout.checkout(c, order["token"])
    return form["fields"]["order_id"], form["fields"]["amount"]


async def add_slip(c, order, amount, data=b"%PDF-1.4 slip", key="k"):
    target = await checkout.slip_target(c, order["token"])
    return await checkout.add_slip(c, target, key=key, file_name="slip.pdf", mime="application/pdf", size=len(data), amount=amount,
                                   paid_on=core.today(), reference="ref", note="", sha256=hashlib.sha256(data).hexdigest())


@unittest.skipUnless(URL, "Set PAYMENTS_TEST_DATABASE_URL to a disposable local PostgreSQL database")
class WebsitePaymentSafety(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        local_only()
        self.c = await asyncpg.connect(URL, ssl=False, statement_cache_size=0)
        await prepare(self.c)
        if await self.c.fetchval("SELECT COUNT(*) FROM learners"):
            await self.c.close()
            self.skipTest("Needs an empty ledger (use a separate database from test_payments_integration)")
        self.tx = self.c.transaction()
        await self.tx.start()
        await self.c.execute("""INSERT INTO settings(key,value) VALUES('web_checkout', $1)
            ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value""", {"finance_email": "finance@test.example"})
        self.patches = [patch.object(payhere.settings, k, v) for k, v in PATCHES.items()]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.tx.rollback()
        await self.c.close()

    async def count(self, sql, *args):
        return await self.c.fetchval(sql, *args)

    async def finance_alerts(self):
        return await self.count("SELECT COUNT(*) FROM message_outbox WHERE to_addr='finance@test.example' AND subject LIKE 'Card payment needs review%'")

    # ---- double payments ------------------------------------------------------------------------
    async def test_second_card_payment_on_a_paid_order_is_not_posted_and_finance_is_told(self):
        o = await new_order(self.c)
        first, amount = await card_attempt(self.c, o)
        second, _ = await card_attempt(self.c, o)          # student opened PayHere twice (two tabs / Back)
        self.assertNotEqual(first, second)
        self.assertTrue((await payhere.notify(self.c, signed(first, amount, "PH-A")))["ok"])
        r = await payhere.notify(self.c, signed(second, amount, "PH-B"))
        self.assertFalse(r["ok"])
        self.assertIn("already paid", r["reason"])
        self.assertEqual(await self.count("SELECT COUNT(*) FROM payments"), 1)
        self.assertEqual(await self.count("SELECT COUNT(*) FROM enrolments"), 1)
        self.assertEqual(await self.finance_alerts(), 1)

    async def test_card_after_an_approved_slip_is_not_posted(self):
        o = await new_order(self.c, method="bank_transfer")
        order_id, amount = await card_attempt(self.c, o)    # opened PayHere before uploading the slip
        s = await add_slip(self.c, o, 50000)
        await checkout.approve_slip(self.c, s["id"], "finance@test")
        r = await payhere.notify(self.c, signed(order_id, amount, "PH-C"))
        self.assertFalse(r["ok"])
        self.assertEqual(await self.count("SELECT COUNT(*) FROM payments"), 1)
        self.assertEqual(await self.finance_alerts(), 1)

    async def test_same_payment_id_twice_posts_once(self):
        o = await new_order(self.c)
        order_id, amount = await card_attempt(self.c, o)
        await payhere.notify(self.c, signed(order_id, amount, "PH-D"))
        r = await payhere.notify(self.c, signed(order_id, amount, "PH-D"))
        self.assertEqual(r["reason"], "duplicate payment")
        self.assertEqual(await self.count("SELECT COUNT(*) FROM payments"), 1)
        self.assertEqual(await self.finance_alerts(), 0)

    async def test_slip_cannot_be_approved_twice(self):
        o = await new_order(self.c, method="bank_transfer")
        s = await add_slip(self.c, o, 50000)
        await checkout.approve_slip(self.c, s["id"], "finance@test")
        with self.assertRaises(ConflictError):
            await checkout.approve_slip(self.c, s["id"], "finance@test")
        self.assertEqual(await self.count("SELECT COUNT(*) FROM payments"), 1)

    async def test_same_slip_file_is_refused(self):
        o = await new_order(self.c, code="monthly", amount=10000, method="bank_transfer")
        await add_slip(self.c, o, 4000, data=b"%PDF-1.4 one")
        with self.assertRaises(ConflictError):
            await add_slip(self.c, o, 4000, data=b"%PDF-1.4 one")
        await add_slip(self.c, o, 6000, data=b"%PDF-1.4 two")   # a different file is fine
        self.assertEqual(await self.count("SELECT COUNT(*) FROM bank_slips"), 2)

    async def test_card_is_blocked_while_a_slip_is_waiting(self):
        o = await new_order(self.c, method="bank_transfer")
        await add_slip(self.c, o, 50000)
        view = await checkout.pay_view(self.c, o["token"])
        self.assertFalse(view["card_enabled"])
        with self.assertRaises(ConflictError):
            await checkout.checkout(self.c, o["token"])

    async def test_card_is_blocked_on_an_invoice_while_its_slip_is_waiting(self):
        o = await new_order(self.c, code="monthly", amount=10000, method="bank_transfer")
        s = await add_slip(self.c, o, 4000, data=b"%PDF-1.4 part")
        await checkout.approve_slip(self.c, s["id"], "finance@test")       # part paid: registration invoice still open
        await add_slip(self.c, o, 6000, data=b"%PDF-1.4 rest")
        self.assertFalse((await checkout.pay_view(self.c, o["token"]))["card_enabled"])
        with self.assertRaises(ConflictError):
            await checkout.checkout(self.c, o["token"])

    # ---- the same order submitted twice (refresh, timeout, two tabs) ----------------------------
    async def test_same_order_submitted_twice_reuses_the_first(self):
        a = await new_order(self.c)
        b = await new_order(self.c)
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(b.get("reused"))
        self.assertEqual(await self.count("SELECT COUNT(*) FROM web_orders"), 1)

    async def test_different_choices_make_a_new_order(self):
        a = await new_order(self.c)
        b = await new_order(self.c, method="bank_transfer")
        c = await new_order(self.c, amount=45000)
        d = await new_order(self.c, phone="0712222222")
        self.assertEqual(len({a["id"], b["id"], c["id"], d["id"]}), 4)

    async def test_paid_or_old_orders_are_not_reused(self):
        a = await new_order(self.c)
        order_id, amount = await card_attempt(self.c, a)
        await payhere.notify(self.c, signed(order_id, amount, "PH-E"))
        b = await new_order(self.c)                       # second enrolment after paying is a new order
        self.assertNotEqual(a["id"], b["id"])
        await self.c.execute("UPDATE web_orders SET created_at=now()-interval '2 hours' WHERE id=$1", b["id"])
        c = await new_order(self.c)
        self.assertNotEqual(b["id"], c["id"])

    # ---- bad or unusual callbacks ---------------------------------------------------------------
    async def test_bad_callbacks_are_rejected_or_ignored(self):
        o = await new_order(self.c)
        order_id, amount = await card_attempt(self.c, o)
        self.assertEqual((await payhere.notify(self.c, signed(order_id, amount, "PH-F", secret="wrong")))["reason"], "invalid signature")
        self.assertIn("mismatch", (await payhere.notify(self.c, signed(order_id, "1.00", "PH-G")))["reason"])
        self.assertIn("mismatch", (await payhere.notify(self.c, signed(order_id, amount, "PH-H", currency="USD")))["reason"])
        for status in ("0", "-1", "-2", "-3"):
            self.assertTrue((await payhere.notify(self.c, signed(order_id, amount, "PH-S" + status, status=status)))["reason"].startswith("status"))
        self.assertEqual(await self.count("SELECT COUNT(*) FROM payments"), 0)
        self.assertEqual((await self.c.fetchrow("SELECT status FROM web_orders WHERE id=$1", o["id"]))["status"], "pending")
        # Captured money with the wrong amount still needs a person to look at it.
        self.assertEqual(await self.finance_alerts(), 2)

    async def test_locked_period_keeps_the_callback_for_review(self):
        o = await new_order(self.c)
        order_id, amount = await card_attempt(self.c, o)
        await self.c.execute("INSERT INTO fiscal_periods(period_start,status) VALUES($1,'locked') ON CONFLICT(period_start) DO UPDATE SET status='locked'",
                             core.month_start(core.today()))
        r = await payhere.notify(self.c, signed(order_id, amount, "PH-L"))
        self.assertFalse(r["ok"])
        self.assertEqual(await self.count("SELECT COUNT(*) FROM payments"), 0)
        self.assertEqual(await self.count("SELECT COUNT(*) FROM gateway_events WHERE payment_id='PH-L' AND outcome='rejected'"), 1)
        self.assertEqual(await self.finance_alerts(), 1)

    async def test_overpaid_slip_keeps_the_extra_as_credit(self):
        o = await new_order(self.c, method="bank_transfer")
        s = await add_slip(self.c, o, 55000)
        await checkout.approve_slip(self.c, s["id"], "finance@test")
        inv = await self.c.fetchrow("SELECT status, amount, amount_paid FROM invoices")
        self.assertEqual((inv["status"], inv["amount_paid"]), ("paid", inv["amount"]))
        self.assertEqual(await self.count("SELECT COALESCE(SUM(credit),0) FROM journal_lines WHERE account_code='2110'"), 5000)

    # ---- the daily job and reminders ------------------------------------------------------------
    async def daily(self, day):
        with patch.object(core, "today", return_value=day), patch.object(jobs, "transaction") as tx:
            tx.return_value.__aenter__.return_value = self.c
            tx.return_value.__aexit__.return_value = False
            return await jobs.daily(day)

    async def test_daily_job_twice_issues_once_and_reminds_once_per_stage(self):
        with patch.object(core, "today", return_value="2027-01-10"):
            o = await new_order(self.c, code="monthly", amount=10000)
            order_id, amount = await card_attempt(self.c, o)
            await payhere.notify(self.c, signed(order_id, amount, "PH-M"))
        await self.daily("2027-02-05")                     # month 1 is due 2027-02-10, invoiced 5 days ahead
        await self.daily("2027-02-05")
        self.assertEqual(await self.count("SELECT COUNT(*) FROM invoices WHERE kind='instalment'"), 1)
        self.assertEqual(await self.count("SELECT COUNT(*) FROM payment_links WHERE status='sent'"), 1)
        for _ in range(2):
            await self.daily("2027-02-07")                 # 3 days before
        for _ in range(2):
            await self.daily("2027-02-10")                 # due day
        reminders = await self.count("SELECT COUNT(*) FROM message_outbox m JOIN payment_links l ON l.id=m.payment_link_id WHERE m.subject NOT LIKE '%invoice%'")
        self.assertEqual(await self.count("SELECT reminders_sent FROM payment_links WHERE status='sent'"), 2)
        self.assertLessEqual(reminders, 3)                 # issue email may share the link; reminders are one per stage

    # ---- cancelled and expired orders -----------------------------------------------------------
    async def test_cancelled_order_cannot_be_paid_and_expired_still_can(self):
        o = await new_order(self.c)
        await checkout.cancel_order(self.c, o["id"], "finance@test")
        with self.assertRaises(NotFoundError):
            await checkout.checkout(self.c, o["token"])
        e = await new_order(self.c, amount=40000)
        await self.c.execute("UPDATE web_orders SET status='expired' WHERE id=$1", e["id"])
        order_id, amount = await card_attempt(self.c, e)
        self.assertTrue((await payhere.notify(self.c, signed(order_id, amount, "PH-X")))["ok"])

    async def test_callback_for_a_cancelled_order_is_kept_for_review(self):
        o = await new_order(self.c)
        order_id, amount = await card_attempt(self.c, o)
        await checkout.cancel_order(self.c, o["id"], "finance@test")
        r = await payhere.notify(self.c, signed(order_id, amount, "PH-Y"))
        self.assertFalse(r["ok"])
        self.assertEqual(await self.count("SELECT COUNT(*) FROM payments"), 0)
        self.assertEqual(await self.finance_alerts(), 1)


@unittest.skipUnless(URL, "Set PAYMENTS_TEST_DATABASE_URL to a disposable local PostgreSQL database")
class SimultaneousCallbacks(unittest.IsolatedAsyncioTestCase):
    """Two PayHere callbacks for the same payment arriving together, on separate connections, as on the server."""

    async def asyncSetUp(self):
        local_only()
        self.name = "webpay_race_" + uuid.uuid4().hex[:8]
        base = urlparse(URL)
        admin = await asyncpg.connect(urlunparse(base._replace(path="/postgres")), ssl=False)
        await admin.execute(f'CREATE DATABASE "{self.name}"')
        await admin.close()
        self.url = urlunparse(base._replace(path="/" + self.name))
        self.patches = [patch.object(payhere.settings, k, v) for k, v in PATCHES.items()]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        base = urlparse(URL)
        admin = await asyncpg.connect(urlunparse(base._replace(path="/postgres")), ssl=False)
        await admin.execute(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')
        await admin.close()

    async def test_two_callbacks_at_once_post_one_payment(self):
        c = await asyncpg.connect(self.url, ssl=False, statement_cache_size=0)
        await prepare(c)
        async with c.transaction():
            o = await new_order(c)
            order_id, amount = await card_attempt(c, o)
        await c.close()

        async def callback(payment_id):
            conn = await asyncpg.connect(self.url, ssl=False, statement_cache_size=0)
            await db.codecs(conn)
            try:
                async with conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock($1)", MUTATION_LOCK)   # same lock as transaction(write=True)
                    return await payhere.notify(conn, signed(order_id, amount, payment_id))
            except (APIError, asyncpg.UniqueViolationError) as exc:
                return {"ok": False, "reason": type(exc).__name__}
            finally:
                await conn.close()

        results = await asyncio.gather(callback("PH-R"), callback("PH-R"), callback("PH-R2"))
        c = await asyncpg.connect(self.url, ssl=False, statement_cache_size=0)
        payments = await c.fetchval("SELECT COUNT(*) FROM payments.payments")
        enrolments = await c.fetchval("SELECT COUNT(*) FROM payments.enrolments")
        await c.close()
        self.assertEqual(sum(1 for r in results if r.get("web_order")), 1, results)     # recorded once
        self.assertEqual((payments, enrolments), (1, 1))


if __name__ == "__main__":
    unittest.main()
