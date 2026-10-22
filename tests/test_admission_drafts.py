import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.modules.academic import service
from app.modules.academic.schemas import AdmissionDraftCreate


def result(first=None, one=None):
    value = MagicMock()
    value.mappings.return_value.first.return_value = first
    value.mappings.return_value.one.return_value = one
    return value


@pytest.mark.parametrize("has_course", [True, False])
@pytest.mark.parametrize("existing", [None, {"lead_id": 42, "notes": "Saved via Website Admission Form"}])
def test_save_exit_persists_crm_lead_and_never_emails(has_course, existing):
    payload = AdmissionDraftCreate(
        submission_id=uuid4(), full_name="Future Student", email="student@example.com",
        phone="+94712345678", highest_qualification="After A/L",
        programme_id=1 if has_course else None, preferred_course_id=2 if has_course else None,
        preferred_study_mode="full_time",
    )
    steps = [result(), result(first=existing)]
    if has_course:
        steps.append(result(first={"programme_name": "BCA", "course_name": "BCA Cyber Security", "awarding_body": "Jain", "price": 480000, "duration": "3 years"}))
    steps.extend([result(), result(one={"lead_id": 42}), result(), result()])
    db = SimpleNamespace(execute=AsyncMock(side_effect=steps), commit=AsyncMock(), rollback=AsyncMock())
    with (
        patch("app.modules.crm.service._ensure_counsellor_status_table", AsyncMock()),
        patch("app.modules.crm.service.apply_auto_assignment", AsyncMock()) as assign,
        patch.object(service, "send_public_form_email", AsyncMock()) as mail,
        patch.object(service, "_send_visitor_confirmation", AsyncMock()) as confirmation,
    ):
        response = asyncio.run(service.save_admission_draft(db, payload))
    assert response["lead_id"] == 42
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    mail.assert_not_awaited()
    confirmation.assert_not_awaited()
    assert assign.await_count == (0 if existing else 1)
    upsert = next(call for call in db.execute.await_args_list if "WITH updated AS" in str(call.args[0]))
    assert upsert.args[1]["email"] == "student@example.com"
    assert "application incomplete" in upsert.args[1]["notes"]
    assert upsert.args[1]["interested_course"] == ("BCA Cyber Security" if has_course else None)


def test_save_exit_failure_does_not_report_success_or_send_email():
    db = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("offline")), commit=AsyncMock(), rollback=AsyncMock())
    payload = AdmissionDraftCreate(submission_id=uuid4(), full_name="Student", email="student@example.com", phone="1234567", highest_qualification="After A/L")
    with patch("app.modules.crm.service._ensure_counsellor_status_table", AsyncMock()), patch.object(service, "send_public_form_email", AsyncMock()) as mail, pytest.raises(RuntimeError):
        asyncio.run(service.save_admission_draft(db, payload))
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()
    mail.assert_not_awaited()


def test_saved_complete_application_is_not_downgraded_by_save_exit():
    db = SimpleNamespace(execute=AsyncMock(side_effect=[result(), result(first={"lead_id": 42, "notes": "Submitted via Website Admission Form"})]), commit=AsyncMock(), rollback=AsyncMock())
    payload = AdmissionDraftCreate(submission_id=uuid4(), full_name="Student", email="student@example.com", phone="1234567", highest_qualification="After A/L")
    with patch("app.modules.crm.service._ensure_counsellor_status_table", AsyncMock()), patch.object(service, "send_public_form_email", AsyncMock()) as mail:
        response = asyncio.run(service.save_admission_draft(db, payload))
    assert response["lead_id"] == 42
    assert db.execute.await_count == 2
    mail.assert_not_awaited()
