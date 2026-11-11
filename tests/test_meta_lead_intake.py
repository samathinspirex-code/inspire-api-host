import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.core.errors import ValidationError
from app.modules.crm import meta_leads, public_router, source_assignment
from app.modules.crm.schemas import CrmAssignmentSettingsUpdate, CrmCounsellorOut


def run(coro):
    return asyncio.run(coro)


def lead(**extra):
    return {"id": "1234567890123456", "created_time": "2026-11-10T05:00:00+0000", "form_id": "99", "platform": "ig", "ad_name": "HND reel", "campaign_name": "ATHE Overall",
            "field_data": [{"name": "full_name", "values": ["Nimal Perera"]}, {"name": "phone_number", "values": ["+94771234567"]}, {"name": "email", "values": ["Nimal@Example.com "]},
                           {"name": "which_programme_are_you_interested_in?", "values": ["HND Computing"]}, {"name": "preferred_time", "values": ["Evening"]}], **extra}


def test_answers_map_to_crm_fields_and_the_rest_become_the_message():
    mapped = meta_leads.map_lead(lead(), "HND form")
    assert mapped["full_name"] == "Nimal Perera" and mapped["phone"] == "+94771234567" and mapped["email"] == "nimal@example.com"
    assert mapped["interested_programme"] == "HND Computing" and mapped["campaign"] == "ATHE Overall"
    assert mapped["message"] == "preferred time: Evening" and mapped["social_lead_id"] == "1234567890123456"
    assert "Platform: Instagram" in mapped["notes"] and "HND form" in mapped["notes"]


def test_first_and_last_names_and_missing_answers():
    mapped = meta_leads.map_lead({"id": "1", "field_data": [{"name": "first_name", "values": ["Ama"]}, {"name": "last_name", "values": ["Silva"]}], "platform": "msgr"}, None)
    assert mapped["full_name"] == "Ama Silva" and mapped["phone"] is None and mapped["message"] is None and "Messenger" in mapped["notes"]
    assert meta_leads.map_lead({"id": "2", "field_data": []}, None)["full_name"] == "Meta lead"


def test_intake_is_off_until_configured():
    with patch.object(meta_leads, "settings", SimpleNamespace(CONNECTORS_META_LEADS_TOKEN="", CONNECTORS_META_PAGE_ID="1")):
        assert "skipped" in run(meta_leads.run_once(MagicMock(side_effect=AssertionError("no session"))))


def test_meta_failure_message_never_contains_the_token():
    class Session:
        def __init__(self):
            self.db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), scalar=AsyncMock(return_value=__import__("datetime").datetime(2026, 11, 10)))

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *_):
            return False
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": {"code": 190, "message": "private-leads-token expired"}}))
    conf = SimpleNamespace(CONNECTORS_META_LEADS_TOKEN="private-leads-token", CONNECTORS_META_PAGE_ID="123")
    with patch.object(meta_leads, "settings", conf):
        result = run(meta_leads.run_once(Session, transport=transport))
    assert "rejected the token" in result["error"] and "private" not in result["error"]


def test_meta_requests_keep_the_token_in_the_header():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await meta_leads._pages(client, "123/leadgen_forms", "private-leads-token", {"fields": "id"})
    assert run(go()) == []
    assert seen[0].headers["Authorization"] == "Bearer private-leads-token" and "private" not in str(seen[0].url)


def test_old_public_meta_route_now_needs_the_secret():
    payload = public_router.MetaLeadSubmission(full_name="X", phone="0771234567")
    with patch.object(public_router, "settings", SimpleNamespace(CRM_INTEGRATION_API_TOKEN="s" * 40)):
        for secret in (None, "wrong"):
            with pytest.raises(HTTPException) as exc:
                run(public_router.submit_meta_lead(payload, secret, MagicMock()))
            assert exc.value.status_code == 401


def test_settings_update_accepts_either_field():
    assert CrmAssignmentSettingsUpdate(meta_counsellor_ids=[5]).auto_assign_enabled is None
    assert CrmAssignmentSettingsUpdate(auto_assign_enabled=False).meta_counsellor_ids is None


def test_only_registered_counsellors_can_be_chosen():
    db = SimpleNamespace(scalar=AsyncMock(return_value=True), execute=AsyncMock())
    with pytest.raises(ValidationError):
        run(source_assignment.set_meta_counsellor_ids(db, [5, 999], {5, 6}))
    db.execute.assert_not_called()
    run(source_assignment.set_meta_counsellor_ids(db, [6, 5, 6], {5, 6}))
    assert db.execute.await_args.args[1]["ids"] == [6, 5]


def test_setting_needs_the_migration_first():
    db = SimpleNamespace(scalar=AsyncMock(return_value=False), execute=AsyncMock())
    with pytest.raises(ValidationError):
        run(source_assignment.set_meta_counsellor_ids(db, [5], {5}))
    assert run(source_assignment.meta_counsellor_ids(db)) == []


def test_other_sources_never_use_the_meta_setting():
    db = SimpleNamespace(scalar=AsyncMock(side_effect=AssertionError("must not query")))
    for source in ("manual", "meta", "whatsapp", None):
        assert run(source_assignment.assign_by_source(db, 1, source)) is None


def test_unique_lead_ids_and_new_leads_only_are_built_in():
    sql = (meta_leads.__file__.replace("meta_leads.py", "sql/2026_11_10_meta_lead_intake.sql"))
    text = open(sql, encoding="utf-8").read()
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_leads_social_lead_id" in text and "WHERE social_lead_id IS NOT NULL" in text


def test_page_token_comes_from_the_system_user_token():
    def handle(request):
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "777"})
        return httpx.Response(200, json={"data": [{"id": "555", "access_token": "page-token"}]})

    async def go(page):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await meta_leads._page_token(client, "user-token", page)
    assert run(go("555")) == "page-token"
    assert run(go("777")) == "user-token"  # Already a Page token.
    with pytest.raises(meta_leads.ProviderFailure) as exc:
        run(go("404"))
    assert "Assign the Page" in str(exc.value)


def test_real_form_question_names_map_correctly():
    # Question names from the live "PG athe Lead form".
    mapped = meta_leads.map_lead({"id": "9", "is_organic": True, "field_data": [
        {"name": "full_name", "values": ["Kasun"]}, {"name": "email", "values": ["k@x.lk"]},
        {"name": "what_is_your_highest_qualification?", "values": ["Degree"]}, {"name": "are_you_currently_employed?", "values": ["Yes"]},
        {"name": "what_was_your_field_of_study?", "values": ["Biology"]},
        {"name": "which_postgraduate_programme_are_you_interested_in?", "values": ["ATHE Level 7"]},
        {"name": "phone", "values": ["0771234567"]}, {"name": "job_title", "values": ["Teacher"]},
        {"name": "inbox_url", "values": ["https://business.facebook.com/latest/inbox/123"]}]}, "PG athe Lead form")
    assert mapped["phone"] == "0771234567" and mapped["interested_programme"] == "ATHE Level 7" and mapped["highest_qualification"] == "Degree"
    assert mapped["message"].split("\n") == ["are you currently employed: Yes", "what was your field of study: Biology", "job title: Teacher"]
    assert mapped["platform"] == "Messenger" and "Messenger conversation: https://business.facebook.com/latest/inbox/123" in mapped["notes"]


def test_a_non_link_inbox_value_is_not_trusted_as_a_link():
    mapped = meta_leads.map_lead({"id": "9", "field_data": [{"name": "inbox_url", "values": ["javascript:alert(1)"]}]}, None)
    assert "Messenger conversation" not in mapped["notes"] and mapped["platform"] == "Meta"
