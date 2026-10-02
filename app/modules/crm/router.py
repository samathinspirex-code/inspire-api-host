from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.database import get_db
from app.core.errors import APIError, ForbiddenError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.crm import service
from app.modules.crm.schemas import (
    CrmActivityCreate,
    CrmActivityOut,
    CrmAssignmentSettingsOut,
    CrmAssignmentSettingsUpdate,
    CrmCounsellorOrderUpdate,
    CrmCounsellorOut,
    CrmCounsellorReportResponse,
    CrmCounsellorRosterItem,
    CrmCounsellorStatusUpdate,
    CrmDashboardResponse,
    CrmLeadCreate,
    CrmLeadFilterOptions,
    CrmLeadListResponse,
    CrmLeadOut,
    CrmLeadSummary,
    CrmLeadUpdate,
    CrmPipelineResponse,
    CrmStageUpdate,
)


def require_crm_staff(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    allowed = {"CRM", "COUNSELLOR", "SUPER_ADMIN", "ADMIN", "USER_MANAGEMENT"}
    if not any(role in current_user.access for role in allowed):
        raise ForbiddenError("Requires 'CRM' or administrative access")
    return current_user


def _counsellor_only(user: CurrentUser) -> bool:
    return "COUNSELLOR" in user.access and not any(
        role in user.access for role in ("CRM", "ADMIN", "SUPER_ADMIN", "USER_MANAGEMENT")
    )


async def _check_lead_access(db: AsyncSession, lead_id: int, user: CurrentUser) -> None:
    if _counsellor_only(user):
        owner = await db.scalar(text("SELECT assigned_counsellor_id FROM crm_leads WHERE lead_id=:id"), {"id": lead_id})
        if owner != user.user_id:
            raise ForbiddenError("This lead is not assigned to you")


def _require_management(user: CurrentUser) -> None:
    if _counsellor_only(user):
        raise ForbiddenError("CRM management access required")


router = APIRouter(
    prefix="/crm",
    tags=["crm"],
    dependencies=[Depends(require_crm_staff)],
)


@router.get("/programmes")
async def programme_choices(db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await service.list_programme_choices(db)


@router.get("/counsellors", response_model=list[CrmCounsellorOut])
async def list_counsellors(db: AsyncSession = Depends(get_db)) -> list[CrmCounsellorOut]:
    return await service.list_counsellors(db)


@router.get("/counsellors/roster", response_model=list[CrmCounsellorRosterItem])
async def list_counsellor_roster(db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_crm_staff)) -> list[CrmCounsellorRosterItem]:
    _require_management(current_user)
    return await service.list_counsellor_roster(db)


@router.get("/assignment-settings", response_model=CrmAssignmentSettingsOut)
async def get_assignment_settings(db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_crm_staff)) -> CrmAssignmentSettingsOut:
    _require_management(current_user)
    return await service.get_assignment_settings(db)


@router.patch("/assignment-settings", response_model=CrmAssignmentSettingsOut)
async def update_assignment_settings(
    payload: CrmAssignmentSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_crm_staff),
) -> CrmAssignmentSettingsOut:
    _require_management(current_user)
    return await service.update_assignment_settings(db, payload.auto_assign_enabled)


@router.put("/counsellors/order", response_model=list[CrmCounsellorRosterItem])
async def set_counsellor_order(
    payload: CrmCounsellorOrderUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_crm_staff),
) -> list[CrmCounsellorRosterItem]:
    _require_management(current_user)
    return await service.set_counsellor_order(db, payload.user_ids)


@router.patch("/counsellors/{user_id}", response_model=CrmCounsellorRosterItem)
async def set_counsellor_active(
    user_id: int,
    payload: CrmCounsellorStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_crm_staff),
) -> CrmCounsellorRosterItem:
    _require_management(current_user)
    return await service.set_counsellor_active(db, user_id, payload.is_active)


@router.get("/leads", response_model=CrmLeadListResponse)
async def list_leads(
    stage: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    sort: str = Query("newest"),
    counsellor_id: Optional[int] = Query(None),
    awarding_body: Optional[str] = Query(None),
    programme: Optional[str] = Query(None),
    unassigned: bool = Query(False),
    search: Optional[str] = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    include_archived: bool = Query(False),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    city: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    campaign: Optional[str] = Query(None),
    intake: Optional[str] = Query(None),
    modified_from: Optional[date] = Query(None),
    modified_to: Optional[date] = Query(None),
    contacted_from: Optional[date] = Query(None),
    contacted_to: Optional[date] = Query(None),
    followup_from: Optional[date] = Query(None),
    followup_to: Optional[date] = Query(None),
    days_since_contact: Optional[int] = Query(None, ge=0),
    followup_state: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_crm_staff),
) -> CrmLeadListResponse:
    if _counsellor_only(current_user):
        counsellor_id, unassigned = current_user.user_id, False
    created_from = datetime.combine(date_from, time.min) if date_from else None
    created_to = datetime.combine(date_to + timedelta(days=1), time.min) if date_to else None
    extra = {"city": city, "country": country, "campaign": campaign, "intake": intake, "priority": priority, "sort": sort,
             "days_since_contact": days_since_contact, "followup_state": followup_state}
    for field, start, end in (
        ("updated_at", modified_from, modified_to),
        ("last_contacted_at", contacted_from, contacted_to),
        ("followup_date", followup_from, followup_to),
    ):
        if start:
            extra[f"{field}_from"] = datetime.combine(start, time.min)
        if end:
            extra[f"{field}_to"] = datetime.combine(end + timedelta(days=1), time.min)
    return await service.list_leads(
        db,
        stage,
        source,
        counsellor_id,
        search,
        page,
        size,
        include_archived,
        awarding_body,
        programme,
        unassigned,
        created_from,
        created_to,
        extra,
    )


@router.get("/leads/filters", response_model=CrmLeadFilterOptions)
async def list_lead_filters(db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_crm_staff)) -> CrmLeadFilterOptions:
    return await service.list_lead_filters(db, current_user.user_id if _counsellor_only(current_user) else None)


@router.post("/leads", response_model=CrmLeadOut, status_code=201)
async def create_lead(
    payload: CrmLeadCreate,
    current_user: CurrentUser = Depends(require_crm_staff),
    db: AsyncSession = Depends(get_db),
) -> CrmLeadOut:
    if _counsellor_only(current_user) and payload.assigned_counsellor_id is not None:
        raise ForbiddenError("Only management can choose a counsellor")
    counsellor_name = await service._actor_name(db, current_user.user_id, current_user.email)
    return await service.create_lead(
        db,
        payload,
        counsellor_id=current_user.user_id,
        counsellor_name=counsellor_name,
    )


@router.get("/leads/pipeline", response_model=CrmPipelineResponse)
async def get_pipeline(
    preview_per_stage: int = Query(20, ge=1, le=20),
    counsellor_id: Optional[int] = Query(None),
    awarding_body: Optional[str] = Query(None),
    programme: Optional[str] = Query(None),
    unassigned: bool = Query(False),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_crm_staff),
) -> CrmPipelineResponse:
    if _counsellor_only(current_user):
        counsellor_id, unassigned = current_user.user_id, False
    created_from = datetime.combine(date_from, time.min) if date_from else None
    created_to = datetime.combine(date_to + timedelta(days=1), time.min) if date_to else None
    return await service.get_pipeline(
        db,
        preview_per_stage,
        counsellor_id,
        awarding_body,
        programme,
        unassigned,
        created_from,
        created_to,
    )


@router.get("/leads/dashboard", response_model=CrmDashboardResponse)
async def get_dashboard(
    counsellor_id: int | None = Query(None, gt=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_crm_staff),
) -> CrmDashboardResponse:
    scoped_id = current_user.user_id if _counsellor_only(current_user) else counsellor_id
    return await service.get_dashboard(db, scoped_id)


@router.get("/leads/followups", response_model=list[CrmLeadSummary])
async def get_followups(db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_crm_staff)) -> list[CrmLeadSummary]:
    followups = await service.get_followups(db)
    return [lead for lead in followups if lead.assigned_counsellor_id == current_user.user_id] if _counsellor_only(current_user) else followups


@router.get("/reports/counsellors", response_model=CrmCounsellorReportResponse)
async def counsellor_report(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    counsellor_id: Optional[int] = Query(None),
    programme: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_crm_staff),
) -> CrmCounsellorReportResponse:
    if _counsellor_only(current_user):
        raise ForbiddenError("Counsellor reports require CRM management access")
    try:
        return await service.get_counsellor_report(db, date_from, date_to, counsellor_id, programme)
    except APIError:
        raise
    except Exception as exc:
        raise APIError(500, "REPORT_FAILED", f"Counsellor report failed: {exc}") from exc


@router.get("/leads/export")
async def export_leads_csv(
    stage: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    counsellor_id: Optional[int] = Query(None),
    awarding_body: Optional[str] = Query(None),
    programme: Optional[str] = Query(None),
    unassigned: bool = Query(False),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    city: Optional[str] = Query(None),
    intake: Optional[str] = Query(None),
    followup_state: Optional[str] = Query(None),
    modified_from: Optional[date] = Query(None),
    modified_to: Optional[date] = Query(None),
    contacted_from: Optional[date] = Query(None),
    contacted_to: Optional[date] = Query(None),
    followup_from: Optional[date] = Query(None),
    followup_to: Optional[date] = Query(None),
    format: Optional[str] = Query("template"),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_crm_staff),
) -> StreamingResponse:
    if _counsellor_only(current_user):
        counsellor_id, unassigned = current_user.user_id, False
    created_from = datetime.combine(date_from, time.min) if date_from else None
    created_to = datetime.combine(date_to + timedelta(days=1), time.min) if date_to else None
    extra = {"priority": priority, "city": city, "intake": intake, "followup_state": followup_state}
    for field, start, end in (
        ("updated_at", modified_from, modified_to),
        ("last_contacted_at", contacted_from, contacted_to),
        ("followup_date", followup_from, followup_to),
    ):
        if start:
            extra[f"{field}_from"] = datetime.combine(start, time.min)
        if end:
            extra[f"{field}_to"] = datetime.combine(end + timedelta(days=1), time.min)
    csv_data = await service.export_leads_csv(
        db, stage, source, export_format=format,
        counsellor_id=counsellor_id, awarding_body=awarding_body,
        programme=programme, unassigned=unassigned,
        created_from=created_from, created_to=created_to,
        priority=priority, search=search, extra=extra,
    )
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
async def get_lead(lead_id: int, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_crm_staff)) -> CrmLeadOut:
    await _check_lead_access(db, lead_id, current_user)
    return await service.get_lead(db, lead_id)


@router.get("/leads/{lead_id}/admission-document")
async def get_admission_document(lead_id: int, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_crm_staff)) -> dict[str, str | None]:
    await _check_lead_access(db, lead_id, current_user)
    from app.modules.academic.service import get_crm_admission_document
    return await get_crm_admission_document(db, lead_id)


@router.put("/leads/{lead_id}", response_model=CrmLeadOut)
async def update_lead(
    lead_id: int,
    payload: CrmLeadUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_crm_staff),
) -> CrmLeadOut:
    await _check_lead_access(db, lead_id, current_user)
    if _counsellor_only(current_user) and "assigned_counsellor_id" in payload.model_fields_set:
        raise ForbiddenError("Only management can reassign leads")
    actor_name = await service._actor_name(db, current_user.user_id, current_user.email)
    return await service.update_lead(db, lead_id, payload, current_user.user_id, actor_name)


@router.patch("/leads/{lead_id}/stage", response_model=CrmLeadOut)
async def update_stage(
    lead_id: int,
    payload: CrmStageUpdate,
    current_user: CurrentUser = Depends(require_crm_staff),
    db: AsyncSession = Depends(get_db),
) -> CrmLeadOut:
    await _check_lead_access(db, lead_id, current_user)
    counsellor_name = await service._actor_name(db, current_user.user_id, current_user.email)
    return await service.update_stage(
        db,
        lead_id,
        payload.stage,
        counsellor_id=current_user.user_id,
        counsellor_name=counsellor_name,
        status_reason=payload.status_reason,
        status_remarks=payload.status_remarks,
        affordability_reason=payload.affordability_reason,
        expected_intake=payload.expected_intake,
        expected_month=payload.expected_month,
        delay_reason=payload.delay_reason,
        followup_date=payload.followup_date,
    )


@router.delete("/leads/{lead_id}", status_code=204)
async def delete_lead(lead_id: int, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_crm_staff)) -> None:
    if _counsellor_only(current_user):
        raise ForbiddenError("Only management can archive leads")
    await service.delete_lead(db, lead_id)


@router.post("/leads/{lead_id}/activities", response_model=CrmActivityOut, status_code=201)
async def add_activity(
    lead_id: int,
    payload: CrmActivityCreate,
    current_user: CurrentUser = Depends(require_crm_staff),
    db: AsyncSession = Depends(get_db),
) -> CrmActivityOut:
    await _check_lead_access(db, lead_id, current_user)
    counsellor_name = await service._actor_name(db, current_user.user_id, current_user.email)
    return await service.add_activity(
        db,
        lead_id,
        payload,
        counsellor_id=current_user.user_id,
        counsellor_name=counsellor_name,
    )


@router.post("/leads/{lead_id}/enrollment-email", response_model=CrmLeadOut)
async def retry_enrollment_email(
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_crm_staff),
) -> CrmLeadOut:
    await _check_lead_access(db, lead_id, current_user)
    return await service._send_enrollment_email(db, lead_id)
