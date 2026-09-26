import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.errors import APIError
from app.modules.academic import service
from app.modules.academic.schemas import AdmissionApplicationCreate, ContactInquiryCreate


def _mapping_result(*, first=None, one=None):
    result = MagicMock()
    mappings = result.mappings.return_value
    mappings.first.return_value = first
    mappings.one.return_value = one
    return result


async def _contact_submission_keeps_crm_lead_when_alert_email_fails():
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _mapping_result(one={"lead_id": 31}),
            MagicMock(),
        ]),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    payload = ContactInquiryCreate(
        full_name="Website Visitor",
        email="visitor@example.com",
        phone="+94 77 123 4567",
        message="Please call me about a course.",
    )

    with patch.object(
        service,
        "send_public_form_email",
        AsyncMock(return_value=SimpleNamespace(sent=False, error="mail unavailable")),
    ):
        response = await service.send_contact_inquiry(db, payload)

    assert response["sent"] is True
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    assert db.execute.await_args_list[0].args[1]["phone"] == "+94 77 123 4567"


def test_contact_submission_keeps_crm_lead_when_alert_email_fails():
    asyncio.run(_contact_submission_keeps_crm_lead_when_alert_email_fails())


async def _contact_submission_reports_failure_when_crm_lead_cannot_save():
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=RuntimeError("database unavailable")),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    payload = ContactInquiryCreate(
        full_name="Website Visitor",
        email="visitor@example.com",
        phone="+94 77 123 4567",
        message="Please call me about a course.",
    )

    with (
        patch.object(service, "send_public_form_email", AsyncMock()) as send_email,
        pytest.raises(APIError),
    ):
        await service.send_contact_inquiry(db, payload)

    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()
    send_email.assert_not_awaited()


def test_contact_submission_reports_failure_when_crm_lead_cannot_save():
    asyncio.run(_contact_submission_reports_failure_when_crm_lead_cannot_save())


async def _admission_and_main_crm_leads_commit_together():
    existing = _mapping_result(first=None)
    pathway = _mapping_result(first={
        "programme_name": "Computing",
        "course_name": "HND Computing",
        "awarding_body": "ATHE",
        "price": 250000,
        "duration": "18 months",
    })
    crm_lead = _mapping_result(one={"lead_id": 202})
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[existing, pathway, crm_lead, MagicMock()]),
        scalar=AsyncMock(side_effect=[True, True, 101]),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    payload = AdmissionApplicationCreate(
        submission_id=uuid4(),
        full_name="Future Student",
        email="student@example.com",
        phone="+94 71 222 3333",
        highest_qualification="A/L",
        programme_id=1,
        preferred_course_id=2,
        preferred_study_mode="full_time",
    )

    with patch.object(
        service,
        "send_public_form_email",
        AsyncMock(return_value=SimpleNamespace(sent=False, error="mail unavailable")),
    ):
        response = await service.create_admission_lead(db, payload)

    assert response["lead_id"] == 101
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    statements = [str(call.args[0]) for call in db.execute.await_args_list]
    assert any("INSERT INTO crm_leads" in statement for statement in statements)
    assert any("INSERT INTO crm_activities" in statement for statement in statements)


def test_admission_and_main_crm_leads_commit_together():
    asyncio.run(_admission_and_main_crm_leads_commit_together())


async def _admission_rolls_back_if_main_crm_lead_cannot_save():
    existing = _mapping_result(first=None)
    pathway = _mapping_result(first={
        "programme_name": "Computing",
        "course_name": "HND Computing",
        "awarding_body": "ATHE",
        "price": 250000,
        "duration": "18 months",
    })
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[existing, pathway, RuntimeError("crm unavailable")]),
        scalar=AsyncMock(side_effect=[True, True, 101]),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    payload = AdmissionApplicationCreate(
        submission_id=uuid4(),
        full_name="Future Student",
        email="student@example.com",
        phone="+94 71 222 3333",
        highest_qualification="A/L",
        programme_id=1,
        preferred_course_id=2,
        preferred_study_mode="full_time",
    )

    with pytest.raises(RuntimeError, match="crm unavailable"):
        await service.create_admission_lead(db, payload)

    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


def test_admission_rolls_back_if_main_crm_lead_cannot_save():
    asyncio.run(_admission_rolls_back_if_main_crm_lead_cannot_save())
