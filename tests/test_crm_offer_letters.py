import asyncio
import base64
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import APIError, ValidationError
from app.core.public_form_email import FormEmailResult, offer_letter_email_content
from app.modules.crm import offer_service
from app.modules.crm.offer_public_router import OfferDocumentUpload


@pytest.fixture(autouse=True)
def tables_ready():
    with patch.object(offer_service, "_tables_ready", True):
        yield


def test_add_months_clamps_to_month_end():
    assert offer_service.add_months(date(2026, 11, 18), 2) == date(2027, 1, 18)
    assert offer_service.add_months(date(2026, 12, 31), 2) == date(2027, 2, 28)
    assert offer_service.add_months(date(2027, 12, 31), 2) == date(2028, 2, 29)


def test_offer_expires_at_end_of_day_two_months_later_in_colombo():
    day, moment = offer_service.offer_expiry(datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc))  # 19 Nov 01:30 in Colombo
    assert day == date(2027, 1, 19)
    assert moment == datetime(2027, 1, 19, 18, 29, 59, tzinfo=timezone.utc)
    assert offer_service.format_offer_date(day) == "19 January 2027"


def test_offer_email_has_details_bullets_link_and_expiry():
    subject, text, markup = offer_letter_email_content("Nimal", "Nimal <Perera>", "HND in Computing", "https://site/offer-documents/abc", "18 January 2027")
    assert subject == "Congratulations! Your Conditional Offer of Admission – Inspire College"
    assert "Congratulations, Nimal!" in text and "Study Mode: Online" in text and "Offer Status: Conditional Offer" in text
    assert "Submit Your Documents: https://site/offer-documents/abc" in text and "Offer Valid Until: 18 January 2027" in text
    assert "Nimal &lt;Perera&gt;" in markup and "<Perera>" not in markup
    assert markup.count("<li") == 3 and "NIC or Passport" in markup and "Academic Transcript" in markup
    assert 'href="https://site/offer-documents/abc"' in markup and "071 199 3331" in markup
    assert "background:#3f007c" in markup  # branded student layout
    assert text.count("We are pleased to offer you") == 1


def test_missing_fields_are_named():
    assert offer_service.missing_offer_fields({"full_name": " ", "email": None, "interested_course": "", "interested_programme": None}) == ["full name", "email", "programme"]
    assert offer_service.missing_offer_fields({"full_name": "A B", "email": "a@b.c", "interested_course": None, "interested_programme": "HND"}) == []


def _lead_result(lead):
    result = MagicMock()
    result.mappings.return_value.first.return_value = lead
    return result


def test_send_offer_refuses_without_email():
    db = MagicMock()
    db.execute = AsyncMock(return_value=_lead_result({"lead_id": 4, "full_name": "Ann Silva", "email": "", "interested_programme": "HND", "interested_course": None, "assigned_counsellor_id": None}))
    with pytest.raises(ValidationError, match="email"):
        asyncio.run(offer_service.send_offer_letter(db, 4))


def test_send_offer_emails_the_student_and_never_changes_stage():
    lead = {"lead_id": 4, "full_name": "Ann Silva", "email": "ann@example.com", "interested_programme": "HND", "interested_course": "HND in Business", "assigned_counsellor_id": None}
    db = MagicMock()
    db.execute = AsyncMock(return_value=_lead_result(lead))
    db.scalar = AsyncMock(return_value=77)
    db.commit = AsyncMock()
    send = AsyncMock(return_value=FormEmailResult(True))
    with patch.object(offer_service, "send_public_form_email", send), patch.object(offer_service, "offer_summary", AsyncMock(return_value={"ok": True})):
        assert asyncio.run(offer_service.send_offer_letter(db, 4, sent_by=9, sent_by_name="Counsellor")) == {"ok": True}
    kwargs = send.await_args.kwargs
    assert kwargs["to_email"] == "ann@example.com" and kwargs["custom_id"] == "offer-77"
    subject, _text, markup = send.await_args.args
    assert subject.startswith("Congratulations!") and "HND in Business" in markup and "/offer-documents/" in markup
    sql = " ".join(str(call.args[0]) for call in db.execute.await_args_list)
    assert "stage" not in sql.lower()
    assert "'email'" in sql  # timeline entry


def _offer(expires_at):
    return {"offer_id": 3, "lead_id": 4, "programme": "HND", "expires_at": expires_at, "full_name": "Ann Silva", "assigned_counsellor_id": None}


def _upload(**overrides):
    data = b"%PDF-1.4 nic"
    values = {"document_type": "nic_passport", "filename": "nic.pdf", "content_type": "application/pdf", "size_bytes": len(data), "data_base64": base64.b64encode(data).decode()}
    values.update(overrides)
    return OfferDocumentUpload(**values)


def test_upload_rejects_unknown_and_expired_links():
    db = MagicMock()
    with patch.object(offer_service, "_offer_by_token", AsyncMock(return_value=None)):
        with pytest.raises(APIError) as missing:
            asyncio.run(offer_service.public_upload_document(db, "nope", _upload()))
    assert missing.value.status_code == 404
    expired = _offer(datetime.now(timezone.utc) - timedelta(minutes=1))
    with patch.object(offer_service, "_offer_by_token", AsyncMock(return_value=expired)):
        with pytest.raises(APIError) as gone:
            asyncio.run(offer_service.public_upload_document(db, "old", _upload()))
    assert gone.value.status_code == 410


def test_upload_payload_rejects_bad_type_and_oversized_files():
    with pytest.raises(PydanticValidationError):
        _upload(content_type="application/zip")
    with pytest.raises(PydanticValidationError):
        _upload(size_bytes=10_485_761)
    with pytest.raises(PydanticValidationError):
        _upload(document_type="selfie")


def test_upload_stores_privately_logs_and_notifies():
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    store = AsyncMock(return_value="admissions/offer-documents/4/x-nic.pdf")
    notify = AsyncMock()
    with (
        patch.object(offer_service, "_offer_by_token", AsyncMock(return_value=_offer(datetime.now(timezone.utc) + timedelta(days=5)))),
        patch("app.modules.academic.service.store_private_document", store),
        patch.object(offer_service, "_notify_staff", notify),
        patch.object(offer_service, "public_offer_view", AsyncMock(return_value={"status": "active"})),
    ):
        assert asyncio.run(offer_service.public_upload_document(db, "tok", _upload())) == {"status": "active"}
    assert store.await_args.args[1] == "admissions/offer-documents/4"
    sql = " ".join(str(call.args[0]) for call in db.execute.await_args_list)
    assert "crm_lead_documents" in sql and "document_received" in sql
    notify.assert_awaited_once()


def test_staff_notification_goes_to_assigned_counsellor():
    db = MagicMock()
    db.execute = AsyncMock(return_value=_lead_result({"email": "coun@example.com", "full_name": "Coun Sellor"}))
    send = AsyncMock(return_value=FormEmailResult(True))
    offer = {**_offer(datetime.now(timezone.utc)), "assigned_counsellor_id": 12}
    with patch.object(offer_service, "send_public_form_email", send):
        asyncio.run(offer_service._notify_staff(db, offer, "NIC or Passport", "nic.pdf"))
    assert send.await_args.kwargs["to_email"] == "coun@example.com"
    with patch.object(offer_service, "send_public_form_email", send):
        asyncio.run(offer_service._notify_staff(MagicMock(), _offer(datetime.now(timezone.utc)), "NIC or Passport", "nic.pdf"))
    assert send.await_args.kwargs["to_email"] is None  # falls back to the admissions inbox


def test_offer_email_skips_documents_already_received():
    _s, text, markup = offer_letter_email_content("Ann", "Ann Silva", "HND", "https://site/o/t", "1 Jan 2027", {"qualification"})
    assert markup.count("<li") == 2 and "Highest Educational Qualification</strong>" not in markup
    assert "We have already received: Highest Educational Qualification." in text
    assert "Submit Your Documents: https://site/o/t" in text


def test_offer_email_drops_upload_request_when_required_documents_are_in():
    _s, text, markup = offer_letter_email_content("Ann", "Ann Silva", "HND", "https://site/o/t", "1 Jan 2027", {"qualification", "nic_passport"})
    assert "<li" not in markup and "https://site/o/t" not in markup and "https://site/o/t" not in text
    assert "You do not need to submit them again." in text
    assert "Offer Valid Until: 1 Jan 2027" in text
