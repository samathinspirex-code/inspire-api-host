from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import ForbiddenError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.crm import service
from app.modules.crm.schemas import (
    CrmActivityCreate,
    CrmActivityOut,
    CrmCounsellorOut,
    CrmDashboardResponse,
    CrmLeadCreate,
    CrmLeadListResponse,
    CrmLeadOut,
    CrmLeadUpdate,
    CrmPipelineResponse,
    CrmStageUpdate,
)


def require_crm_staff(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    allowed = {"CRM", "COUNSELLOR", "SUPER_ADMIN", "ADMIN", "USER_MANAGEMENT"}
    if not any(role in current_user.access for role in allowed):
        raise ForbiddenError("Requires 'CRM' or administrative access")
    return current_user


router = APIRouter(
    prefix="/crm",
    tags=["crm"],
    dependencies=[Depends(require_crm_staff)],
)


@router.get("/counsellors", response_model=list[CrmCounsellorOut])
async def list_counsellors(db: AsyncSession = Depends(get_db)) -> list[CrmCounsellorOut]:
    return await service.list_counsellors(db)


@router.get("/leads", response_model=CrmLeadListResponse)
async def list_leads(
    stage: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    counsellor_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    include_archived: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> CrmLeadListResponse:
    return await service.list_leads(db, stage, source, counsellor_id, search, page, size, include_archived)


@router.post("/leads", response_model=CrmLeadOut, status_code=201)
async def create_lead(
    payload: CrmLeadCreate,
    current_user: CurrentUser = Depends(require_crm_staff),
    db: AsyncSession = Depends(get_db),
) -> CrmLeadOut:
    counsellor_name = await service._actor_name(db, current_user.user_id, current_user.email)
    return await service.create_lead(
        db,
        payload,
        counsellor_id=current_user.user_id,
        counsellor_name=counsellor_name,
    )


@router.get("/leads/pipeline", response_model=CrmPipelineResponse)
async def get_pipeline(db: AsyncSession = Depends(get_db)) -> CrmPipelineResponse:
    return await service.get_pipeline(db)


@router.get("/leads/dashboard", response_model=CrmDashboardResponse)
async def get_dashboard(db: AsyncSession = Depends(get_db)) -> CrmDashboardResponse:
    return await service.get_dashboard(db)


@router.get("/leads/export")
async def export_leads_csv(
    stage: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    format: Optional[str] = Query("template"),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    csv_data = await service.export_leads_csv(db, stage, source, export_format=format)
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"inspire_crm_pipeline_{date_str}.csv" if format == "template" else f"crm_leads_{date_str}.csv"

    def iterfile():
        yield csv_data

    return StreamingResponse(
        iterfile(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/leads/{lead_id}", response_model=CrmLeadOut)
async def get_lead(lead_id: int, db: AsyncSession = Depends(get_db)) -> CrmLeadOut:
    return await service.get_lead(db, lead_id)


@router.put("/leads/{lead_id}", response_model=CrmLeadOut)
async def update_lead(
    lead_id: int,
    payload: CrmLeadUpdate,
    db: AsyncSession = Depends(get_db),
) -> CrmLeadOut:
    return await service.update_lead(db, lead_id, payload)


@router.patch("/leads/{lead_id}/stage", response_model=CrmLeadOut)
async def update_stage(
    lead_id: int,
    payload: CrmStageUpdate,
    current_user: CurrentUser = Depends(require_crm_staff),
    db: AsyncSession = Depends(get_db),
) -> CrmLeadOut:
    counsellor_name = await service._actor_name(db, current_user.user_id, current_user.email)
    return await service.update_stage(
        db,
        lead_id,
        payload.stage,
        counsellor_id=current_user.user_id,
        counsellor_name=counsellor_name,
    )


@router.delete("/leads/{lead_id}", status_code=204)
async def delete_lead(lead_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await service.delete_lead(db, lead_id)


@router.post("/leads/{lead_id}/activities", response_model=CrmActivityOut, status_code=201)
async def add_activity(
    lead_id: int,
    payload: CrmActivityCreate,
    current_user: CurrentUser = Depends(require_crm_staff),
    db: AsyncSession = Depends(get_db),
) -> CrmActivityOut:
    counsellor_name = await service._actor_name(db, current_user.user_id, current_user.email)
    return await service.add_activity(
        db,
        lead_id,
        payload,
        counsellor_id=current_user.user_id,
        counsellor_name=counsellor_name,
    )
