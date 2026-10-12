"""Read-only CRM endpoints for server-to-server integrations."""

from datetime import date, datetime, time, timedelta
from secrets import compare_digest
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import UnauthorizedError
from app.modules.crm import service
from app.modules.crm.schemas import CrmLeadListResponse, CrmLeadOut


crm_api_key_header = APIKeyHeader(name="X-CRM-API-Key", auto_error=False)


async def require_crm_integration_key(
    x_crm_api_key: Optional[str] = Depends(crm_api_key_header),
) -> None:
    configured_key = settings.CRM_INTEGRATION_API_TOKEN
    if not x_crm_api_key or len(configured_key) < 32 or not compare_digest(x_crm_api_key, configured_key):
        raise UnauthorizedError("Invalid CRM API key")


router = APIRouter(
    prefix="/api/v1/integrations/crm",
    tags=["crm-integrations"],
    dependencies=[Depends(require_crm_integration_key)],
)


@router.get("/leads", response_model=CrmLeadListResponse)
async def list_integration_leads(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    stage: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    modified_from: Optional[date] = Query(None),
    modified_to: Optional[date] = Query(None),
    include_archived: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> CrmLeadListResponse:
    extra: dict = {"sort": "updated"}
    if modified_from:
        extra["updated_at_from"] = datetime.combine(modified_from, time.min)
    if modified_to:
        extra["updated_at_to"] = datetime.combine(modified_to + timedelta(days=1), time.min)
    return await service.list_leads(
        db, stage, source, None, search, page, size, include_archived,
        extra=extra,
    )


@router.get("/leads/{lead_id}", response_model=CrmLeadOut)
async def get_integration_lead(
    lead_id: int,
    db: AsyncSession = Depends(get_db),
) -> CrmLeadOut:
    return await service.get_lead(db, lead_id)
