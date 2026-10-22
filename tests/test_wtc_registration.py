import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import APIError
from app.core.mailjet_smtp import _build_email
from app.modules.academic import wtc_event


def payload(**overrides):
    return wtc_event.WtcRegistrationCreate(**{
        "submission_id": uuid4(), "full_name": "Future Professional", "phone": "+94 77 123 4567",
        "email": "visitor@example.com", "company_name": "Example Company", "employee_id": "E-101",
        "programme": "CPD Short Courses", "visiting_time": "10.00 AM – 12.00 PM", "consent": True,
        **overrides,
    })


def result(first=None, one=None):
    value = MagicMock()
    value.mappings.return_value.first.return_value = first
    value.mappings.return_value.one.return_value = one
    return value


def database(existing=None, fail=False):
    delivery = {"confirmation_sent": False, "notification_sent": False}
    if existing:
        delivery.update({key: existing[key] for key in delivery})
    async def execute(statement, params=None):
        sql = str(statement)
        if fail:
            raise RuntimeError("database offline")
        if "SELECT * FROM wtc_event_registrations" in sql:
            return result(first=existing)
        if "SELECT confirmation_sent, notification_sent" in sql:
            return result(one=dict(delivery))
        if "INSERT INTO crm_leads" in sql:
            return result(one={"lead_id": 42})
        if "UPDATE crm_leads SET full_name" in sql:
            return result(first={"lead_id": 42})
        if "confirmation_sent=FALSE" in sql:
            delivery.update(confirmation_sent=False, notification_sent=False)
        return result()
    return SimpleNamespace(execute=AsyncMock(side_effect=execute), commit=AsyncMock(), rollback=AsyncMock())


def stored(registration, confirmation=True, notification=True):
    return {**registration.model_dump(), "email": str(registration.email), "registration_id": "WTC26-ABC12345", "lead_id": 42,
            "confirmation_sent": confirmation, "notification_sent": notification}


@pytest.mark.parametrize("visitor_sent,admin_sent", [(True,True),(False,True),(True,False),(False,False)])
def test_registration_saves_crm_before_two_independent_emails(visitor_sent,admin_sent):
    db = database()
    async def send(**kwargs):
        assert db.commit.await_count >= 1
        accepted = visitor_sent if kwargs["to_email"] == "visitor@example.com" else admin_sent
        return SimpleNamespace(sent=accepted,error=None if accepted else "offline")
    sender=AsyncMock(side_effect=send)
    with patch("app.modules.crm.service._ensure_counsellor_status_table", AsyncMock()), patch("app.modules.crm.service.apply_auto_assignment", AsyncMock()), patch.object(wtc_event,"send_public_form_email",sender):
        response=asyncio.run(wtc_event.register_wtc_visitor(db,payload()))
    assert response["crm_saved"] is True
    assert response["lead_id"] == 42
    assert response["confirmation_sent"] is visitor_sent
    assert response["notification_sent"] is admin_sent
    assert db.commit.await_count == 2
    db.rollback.assert_not_awaited()
    assert sender.await_count == 2
    visitor,admin=[call.kwargs for call in sender.await_args_list]
    assert visitor["to_email"] == "visitor@example.com"
    assert admin["to_email"] == "enrol@inspire.college"
    assert "cc_email" not in visitor and "cc_email" not in admin
    assert "You’re Registered!" in visitor["subject"]
    assert admin["subject"].startswith("WTC new registration")
    assert "Please check" in admin["text_body"]
    assert "E-101" in admin["text_body"]
    assert response["registration_id"] in visitor["html_body"]
    assert "Tag: WTC registrations form" in admin["text_body"]


def test_duplicate_reuses_id_without_emails_or_new_lead():
    registration=payload();db=database(stored(registration))
    with patch("app.modules.crm.service._ensure_counsellor_status_table",AsyncMock()),patch.object(wtc_event,"send_public_form_email",AsyncMock()) as sender:
        response=asyncio.run(wtc_event.register_wtc_visitor(db,registration))
    assert response["registration_id"] == "WTC26-ABC12345"
    assert response["crm_saved"] is True
    assert not any("INSERT INTO crm_leads" in str(call.args[0]) for call in db.execute.await_args_list)
    sender.assert_not_awaited()


@pytest.mark.parametrize("confirmation,notification,recipient", [(True,False,"enrol@inspire.college"),(False,True,"visitor@example.com")])
def test_duplicate_retries_only_unsent_email(confirmation,notification,recipient):
    registration=payload();db=database(stored(registration,confirmation,notification))
    with patch("app.modules.crm.service._ensure_counsellor_status_table",AsyncMock()),patch.object(wtc_event,"send_public_form_email",AsyncMock(return_value=SimpleNamespace(sent=True,error=None))) as sender:
        response=asyncio.run(wtc_event.register_wtc_visitor(db,registration))
    sender.assert_awaited_once()
    assert sender.await_args.kwargs["to_email"] == recipient
    assert response["confirmation_sent"] and response["notification_sent"]


def test_resubmission_updates_crm_and_registration_without_duplicate():
    original=payload();db=database(stored(original))
    updated=original.model_copy(update={"company_name":"New Company","programme":"ATHE Level 7 Qualifications"})
    with patch("app.modules.crm.service._ensure_counsellor_status_table",AsyncMock()),patch.object(wtc_event,"send_public_form_email",AsyncMock(return_value=SimpleNamespace(sent=True,error=None))) as sender:
        response=asyncio.run(wtc_event.register_wtc_visitor(db,updated))
    assert response["registration_id"] == "WTC26-ABC12345"
    statements=[str(call.args[0]) for call in db.execute.await_args_list]
    assert not any("INSERT INTO crm_leads" in sql for sql in statements)
    assert any("UPDATE crm_leads SET full_name" in sql for sql in statements)
    assert any("company_name=:company_name" in sql for sql in statements)
    assert sender.await_count == 2
    assert "New Company" in sender.await_args.kwargs["text_body"]


def test_email_exception_does_not_prevent_independent_admin_notification():
    db=database()
    sender=AsyncMock(side_effect=[RuntimeError("visitor email offline"),SimpleNamespace(sent=True,error=None)])
    with patch("app.modules.crm.service._ensure_counsellor_status_table",AsyncMock()),patch("app.modules.crm.service.apply_auto_assignment",AsyncMock()),patch.object(wtc_event,"send_public_form_email",sender):
        response=asyncio.run(wtc_event.register_wtc_visitor(db,payload()))
    assert response["crm_saved"] is True
    assert not response["confirmation_sent"] and response["notification_sent"]


def test_database_failure_rolls_back_without_email():
    db=database(fail=True)
    with patch("app.modules.crm.service._ensure_counsellor_status_table",AsyncMock()),patch.object(wtc_event,"send_public_form_email",AsyncMock()) as sender,pytest.raises(APIError):
        asyncio.run(wtc_event.register_wtc_visitor(db,payload()))
    db.commit.assert_not_awaited();db.rollback.assert_awaited_once();sender.assert_not_awaited()


def test_commit_failure_never_returns_success_or_sends_email():
    db=database();db.commit.side_effect=RuntimeError("commit failed")
    with patch("app.modules.crm.service._ensure_counsellor_status_table",AsyncMock()),patch("app.modules.crm.service.apply_auto_assignment",AsyncMock()),patch.object(wtc_event,"send_public_form_email",AsyncMock()) as sender,pytest.raises(APIError):
        asyncio.run(wtc_event.register_wtc_visitor(db,payload()))
    sender.assert_not_awaited()


@pytest.mark.parametrize("values", [{"consent": False}, {"company_name": " "}, {"phone": "abcdefghi"}, {"programme": "Invalid"}, {"visiting_time": "Midnight"}])
def test_invalid_registration_is_rejected(values):
    with pytest.raises(ValidationError):
        payload(**values)


def test_email_escapes_name_and_company():
    _, markup = wtc_event.registration_email(payload(full_name="<script>visitor</script>", company_name="<b>Company</b>"), "WTC26-ABC12345")
    assert "<script>" not in markup
    assert "&lt;script&gt;" in markup
    assert "&lt;b&gt;Company&lt;/b&gt;" in markup


def test_smtp_fallback_includes_copy_recipient():
    email = _build_email({"From": {"Email": "enrol@inspire.college"}, "To": [{"Email": "visitor@example.com"}], "Cc": [{"Email": "enrol@inspire.college"}], "Subject": "WTC Event", "TextPart": "Registered"})
    assert "enrol@inspire.college" in email["Cc"]
    assert "visitor@example.com" in email["To"]
