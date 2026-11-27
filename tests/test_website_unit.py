"""New website module: pure logic (no database)."""
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.core.errors import ValidationError
from app.modules.payments import core
from app.modules.payments.settings_validation import validate
from app.modules.website import checkout, detail, guards, pricing, templates


def catalogue_course(**kw):
    base = dict(course_id=7, slug="ai-mastery", title="AI Mastery", subtitle="CPD | AI Mastery", awarding_body="CPD UK", school_name="School of CPD",
                programme_name="Short Courses", blurb="Learn AI.\nBuild agents.\nShip work.", image_url="https://cdn.example/ai.png",
                entry_requirements="Any background\nA laptop", progression_route="Advanced AI", monthly_instalment=None,
                limited_offer_enabled=False, payment_plans_enabled=False, payment_plans=None,
                topics=[{"order": 1, "topic": "Prompting"}, {"order": 2, "topic": "Agents"}], outcomes=[{"order": 1, "outcome": "AI lead"}],
                study_options=[{"study_mode": "full_time", "price": 45000, "duration": "6 weeks", "is_enabled": True},
                               {"study_mode": "part_time", "price": 50000, "duration": "6 weeks", "is_enabled": False}])
    base.update(kw)
    return base


class ComposeTests(unittest.TestCase):
    def test_generated_course_has_every_page_field(self):
        c = detail.compose(catalogue_course(), None, 3)
        self.assertEqual(c["key"], "ai-mastery")
        self.assertEqual(c["fee"], 45000)                      # disabled option ignored
        self.assertEqual(c["plan_type"], "cpd")
        self.assertEqual(c["fee_label"], "LKR 45,000")
        self.assertEqual(c["sort_order"], 103)
        for k in detail.REQUIRED_DETAIL:
            self.assertNotIn(c["detail"].get(k), (None, "", []), k)
        self.assertEqual(c["detail"]["entry"], "Any background · A laptop")

    def test_design_page_keeps_layout_but_takes_live_fields(self):
        web = dict(design_key="c-aimastery", plan_type="cpd", short_name="AI Mastery", featured=True, sort_order=2,
                   detail={"who": ["Designers"], "cups": "8 coffees", "name": "Old name", "img": "/assets/old.png", "card_name": "AI Mastery card"})
        c = detail.compose(catalogue_course(title="AI Mastery 2027"), web, 0)
        self.assertEqual(c["key"], "c-aimastery")
        self.assertTrue(c["featured"])
        self.assertEqual(c["detail"]["who"], ["Designers"])          # design text kept
        self.assertEqual(c["detail"]["card_name"], "AI Mastery card")
        self.assertEqual(c["detail"]["name"], "AI Mastery 2027")     # live title wins
        self.assertEqual(c["detail"]["img"], "https://cdn.example/ai.png")

    def test_fee_override_and_hidden_fields(self):
        c = detail.compose(catalogue_course(), {"fee_override": Decimal("39000.00"), "plan_type": "cpd"}, 0)
        self.assertEqual(c["fee"], 39000)
        self.assertEqual(c["detail"]["fee"], 39000)

    def test_hnd_level_maps_to_hnd_plan(self):
        c = detail.compose(catalogue_course(programme_name="HND", slug="hnd-x"), None, 0)
        self.assertEqual((c["level"], c["plan_type"]), ("HND", "hnd"))
        self.assertEqual(c["fee_label"], "from LKR 45,000")


class PricingTests(unittest.TestCase):
    def test_server_prices(self):
        hnd = pricing.plans_for({"plan_type": "hnd"})
        terms = pricing.billing_terms(hnd[0], "hnd")
        self.assertEqual((terms["code"], terms["today"], terms["instalments"], terms["instalment_amount"]), ("monthly", 10000, 9, 9999))
        cpd = pricing.plans_for({"plan_type": "cpd", "fee": 45000})
        self.assertEqual(pricing.billing_terms(cpd[0], "cpd")["today"], 45000)
        self.assertEqual(pricing.addon_price({"fee": 40000}), 30000)

    def test_fee_plan_validation(self):
        good = pricing.validate_fee_plans({"hnd": [{"t": "Monthly", "u": "a month", "today": 10000, "instalments": 9, "instalment_amount": 9999}]})
        self.assertEqual(good["hnd"][0]["code"], "monthly")
        with self.assertRaises(ValidationError):
            pricing.validate_fee_plans({"hnd": [{"t": "Full", "code": "full", "today": 0}]})


class MoneyAndDates(unittest.TestCase):
    def test_month_end_schedule_from_payment_date(self):
        self.assertEqual([core.add_months("2027-01-31", i) for i in (1, 2, 3)], ["2027-02-28", "2027-03-31", "2027-04-30"])

    def test_web_checkout_setting_validation(self):
        ok = {"bank": {"account_name": "Inspire College", "bank": "BOC", "branch": "Colombo", "account_no": "123", "note": ""},
              "finance_email": "finance@example.com", "year2": {"instalments": 9, "instalment_amount": 9999}}
        self.assertEqual(validate("web_checkout", ok), ok)
        with self.assertRaises(ValidationError):
            validate("web_checkout", {**ok, "year2": {"instalments": 0, "instalment_amount": 1}})


class SlipFiles(unittest.TestCase):
    def test_magic_bytes(self):
        import base64
        data, mime, ext = checkout.decode_slip(base64.b64encode(b"%PDF-1.7 test").decode())
        self.assertEqual((mime, ext), ("application/pdf", ".pdf"))
        with self.assertRaises(ValidationError):
            checkout.decode_slip(base64.b64encode(b"<html>").decode())


class Guards(unittest.TestCase):
    def test_visitor_ip_needs_the_shared_secret(self):
        req = SimpleNamespace(headers={"x-web-proxy-key": "s3cret", "x-web-visitor-ip": "203.0.113.9"}, client=SimpleNamespace(host="10.0.0.1"))
        with patch.object(guards.settings, "WEB_PROXY_SECRET", "s3cret"):
            self.assertEqual(guards.visitor_ip(req), "203.0.113.9")
        with patch.object(guards.settings, "WEB_PROXY_SECRET", "other"):
            self.assertEqual(guards.visitor_ip(req), "10.0.0.1")


class Emails(unittest.TestCase):
    def test_links_point_at_the_website(self):
        with patch.object(templates.settings, "WEB_SITE_URL", "https://new.example"):
            subject, html, text = templates.invoice_issued("Ama", "INV-10001", "Instalment 1/9", "HND", 9999, "2027-02-28", "tok123")
        self.assertIn("https://new.example/pay/tok123", html)
        self.assertIn("28 Feb 2027", text)


if __name__ == "__main__":
    unittest.main()
