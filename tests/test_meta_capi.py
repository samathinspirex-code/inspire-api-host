import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.modules.connectors import capi, registry

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.mark.parametrize("raw,expected", [
    ("077 123 4567", "94771234567"), ("771234567", "94771234567"), ("+94 77 123 4567", "94771234567"),
    ("0094771234567", "94771234567"), ("+971 50 123 4567", "971501234567"), ("12345", None), ("", None), (None, None),
])
def test_phone_numbers_are_normalised_before_hashing(raw, expected):
    assert capi.normalize_phone(raw) == expected


def test_event_sends_only_hashes_click_ids_and_the_fee():
    row = {"lead_id": 7, "event_name": "Enrolled", "event_time": NOW, "phone": "0771234567", "email": " Student@Example.COM ", "programme_fee": "LKR 250,000",
           "fbc": "fb.1.1700000000.abc", "fbp": "not-a-click-id", "full_name": "Private Person", "notes": "Private note"}
    event = capi.build_event(row)
    assert event["user_data"] == {"ph": [sha("94771234567")], "em": [sha("student@example.com")], "fbc": "fb.1.1700000000.abc"}
    assert event["custom_data"] == {"event_source": "crm", "lead_event_source": "Inspire CMS", "value": 250000.0, "currency": "LKR"}
    assert event["event_id"] == "inspire-7-enrolled" and event["action_source"] == "system_generated"
    text = json.dumps(event)
    assert "Private" not in text and "0771234567" not in text and "tudent@" not in text


def test_lead_events_carry_no_fee_and_no_identifier_means_no_event():
    row = {"lead_id": 7, "event_name": "Lead", "event_time": NOW, "phone": "0771234567", "programme_fee": 5000}
    assert "value" not in capi.build_event(row)["custom_data"]
    assert capi.build_event({"lead_id": 7, "event_name": "Lead", "event_time": NOW, "phone": "x", "email": None}) is None


def test_nothing_runs_while_switched_off():
    factory = MagicMock(side_effect=AssertionError("must not open a session"))
    settings = SimpleNamespace(CONNECTORS_META_CAPI_TOKEN="private-token", CONNECTORS_META_PIXEL_ID="123", CONNECTORS_META_CAPI_ENABLED=False)
    with patch.object(capi, "settings", settings):
        assert "skipped" in run(capi.run_once(factory))


@pytest.mark.parametrize("token,pixel,on,expected", [("t", "1", True, True), ("", "1", True, False), ("t", "", True, False), ("t", "1", False, False)])
def test_sending_needs_token_pixel_and_the_switch(token, pixel, on, expected):
    with patch.object(capi, "settings", SimpleNamespace(CONNECTORS_META_CAPI_TOKEN=token, CONNECTORS_META_PIXEL_ID=pixel, CONNECTORS_META_CAPI_ENABLED=on)):
        assert capi.is_enabled() is expected


def test_no_consent_means_no_send_unless_lead_forms_are_approved():
    row = {"is_archived": False, "has_consent": False, "source": "meta_lead_forms"}
    with patch.object(capi, "settings", SimpleNamespace(CONNECTORS_META_CAPI_LEAD_FORMS=False)):
        assert not capi._allowed(row)
        assert capi._allowed({**row, "has_consent": True})
        assert not capi._allowed({**row, "has_consent": True, "is_archived": True})
    with patch.object(capi, "settings", SimpleNamespace(CONNECTORS_META_CAPI_LEAD_FORMS=True)):
        assert capi._allowed(row)
        assert not capi._allowed({**row, "source": "whatsapp"})


def test_unticked_box_writes_nothing():
    db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
    run(capi.record_consent(db, "contact", "a@b.com", "077", False))
    db.execute.assert_not_called()


def test_consent_failure_never_fails_the_form():
    db = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("private detail")), commit=AsyncMock(), rollback=AsyncMock())
    run(capi.record_consent(db, "contact", "a@b.com", "077", True))
    db.rollback.assert_awaited_once()


def test_bad_click_ids_are_dropped_not_stored():
    db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
    run(capi.record_consent(db, "contact", "A@B.com", " 077 ", True, "<script>", "fb.1.170.xyz"))
    params = db.execute.await_args.args[1]
    assert params["fbc"] is None and params["fbp"] == "fb.1.170.xyz" and params["email"] == "a@b.com" and params["phone"] == "077"


@pytest.mark.parametrize("response,delivered,word", [
    (httpx.Response(200, json={"events_received": 1}), True, None),
    (httpx.Response(400, json={"error": {"message": "private-token bad"}}), False, "rejected this batch"),
    (httpx.Response(401, text="private-token"), False, "rejected the token"),
    (httpx.Response(503, text="private-token"), False, "busy"),
])
def test_token_stays_in_the_header_and_errors_are_fixed_messages(response, delivered, word):
    seen = []

    def handle(request):
        seen.append(request)
        return response
    settings = SimpleNamespace(CONNECTORS_META_CAPI_TOKEN="private-token", CONNECTORS_META_PIXEL_ID="123")
    with patch.object(capi, "settings", settings):
        ok, message = run(capi._post([{"event_name": "Lead"}], httpx.MockTransport(handle)))
    assert ok is delivered
    assert seen[0].headers["Authorization"] == "Bearer private-token"
    assert "private-token" not in str(seen[0].url) and b"private-token" not in seen[0].content
    assert str(seen[0].url).endswith("/123/events")
    if word:
        assert word in message and "private" not in message


def test_test_event_uses_the_test_code_and_no_real_person():
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={})
    settings = SimpleNamespace(CONNECTORS_META_CAPI_TOKEN="t", CONNECTORS_META_PIXEL_ID="123")
    with patch.object(capi, "settings", settings):
        delivered, _ = run(capi.send_test_event("TEST123", transport=httpx.MockTransport(handle)))
    assert delivered and seen[0]["test_event_code"] == "TEST123"
    assert seen[0]["data"][0]["user_data"] == {"ph": [sha("94000000000")]}


def test_status_does_not_query_missing_tables():
    db = SimpleNamespace(scalar=AsyncMock(return_value=False), execute=AsyncMock(side_effect=AssertionError("table is missing")))
    with patch.object(capi, "settings", SimpleNamespace(CONNECTORS_META_CAPI_TOKEN="t", CONNECTORS_META_PIXEL_ID="1", CONNECTORS_META_CAPI_ENABLED=False, CONNECTORS_META_CAPI_LEAD_FORMS=False)):
        result = run(capi.status(db))
    assert result["installed"] is False and result["configured"] is True
    assert "t" != result.get("token")


@pytest.mark.parametrize("state,expected", [
    (dict(configured=False, installed=False, enabled=False, failed_30d=0, last_error=None, sent_30d=0), "Not connected"),
    (dict(configured=True, installed=False, enabled=False, failed_30d=0, last_error=None, sent_30d=0), "Setup needed"),
    (dict(configured=True, installed=True, enabled=False, failed_30d=0, last_error=None, sent_30d=0), "Ready · sending off"),
    (dict(configured=True, installed=True, enabled=True, failed_30d=0, last_error=None, sent_30d=0), "Waiting for events"),
    (dict(configured=True, installed=True, enabled=True, failed_30d=0, last_error=None, sent_30d=3), "Sending events"),
    (dict(configured=True, installed=True, enabled=True, failed_30d=1, last_error="x", sent_30d=3), "Delivery problem"),
])
def test_status_only_claims_sending_after_real_delivery(state, expected):
    assert next(p for p in registry.platforms_for([], capi=state) if p["id"] == "capi")["status"] == expected


def test_old_events_are_never_a_valid_age():
    assert capi.MAX_EVENT_AGE == timedelta(days=7)
