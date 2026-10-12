from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import APIError, api_error_handler, request_validation_handler
from app.modules.crm.integration_router import router
from app.modules.crm.schemas import CrmLeadOut


TOKEN = "iccrm_" + "a" * 43


def make_client():
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(APIError, api_error_handler)
    from fastapi.exceptions import RequestValidationError
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


def test_integration_key_is_required_and_valid_token_can_list_leads():
    client = make_client()
    assert client.get("/api/v1/integrations/crm/leads").status_code == 401
    with patch.object(settings, "CRM_INTEGRATION_API_TOKEN", TOKEN), patch(
        "app.modules.crm.integration_router.service.list_leads", new_callable=AsyncMock
    ) as list_leads:
        list_leads.return_value = {"data": [], "total": 0}
        response = client.get(
            "/api/v1/integrations/crm/leads?page=2&size=10&modified_from=2026-10-01",
            headers={"X-CRM-API-Key": TOKEN},
        )
    assert response.status_code == 200
    assert response.json() == {"data": [], "total": 0}
    args = list_leads.await_args.args
    assert args[5:8] == (2, 10, False)
    assert list_leads.await_args.kwargs["extra"]["updated_at_from"] == datetime(2026, 10, 1)


def test_unconfigured_and_wrong_tokens_cannot_read_leads():
    client = make_client()
    with patch("app.modules.crm.integration_router.service.list_leads", new_callable=AsyncMock) as list_leads:
        for configured in ("", TOKEN):
            with patch.object(settings, "CRM_INTEGRATION_API_TOKEN", configured):
                response = client.get("/api/v1/integrations/crm/leads", headers={"X-CRM-API-Key": "wrong-token"})
            assert response.status_code == 401
            assert response.json()["error"]["message"] == "Invalid CRM API key"
        list_leads.assert_not_awaited()


def test_detail_uses_same_key_and_no_write_route_is_exposed():
    client = make_client()
    with patch.object(settings, "CRM_INTEGRATION_API_TOKEN", TOKEN), patch(
        "app.modules.crm.integration_router.service.get_lead", new_callable=AsyncMock
    ) as get_lead:
        now = datetime.now(timezone.utc)
        get_lead.return_value = CrmLeadOut(
            lead_id=8, full_name="Example", source="manual", stage="new_lead",
            priority="medium", is_archived=False, created_at=now, updated_at=now,
        )
        response = client.get("/api/v1/integrations/crm/leads/8", headers={"X-CRM-API-Key": TOKEN})
    assert response.status_code == 200
    assert response.json()["lead_id"] == 8
    assert get_lead.await_args.args[1] == 8
    assert client.post("/api/v1/integrations/crm/leads", headers={"X-CRM-API-Key": TOKEN}).status_code == 405
