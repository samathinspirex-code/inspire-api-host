import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.crm.models.lead import CrmLead
from app.modules.crm.models.activity import CrmActivity
from app.modules.crm.repository import CrmActivityRepository, CrmLeadRepository
from app.modules.crm.schemas import (
    CrmActivityCreate,
    CrmActivityOut,
    CrmDashboardResponse,
    CrmDashboardStats,
    CrmLeadCreate,
    CrmLeadListResponse,
    CrmLeadOut,
    CrmLeadSummary,
    CrmLeadUpdate,
    CrmPipelineResponse,
    CrmPipelineStage,
)

# Canonical pipeline stage order
PIPELINE_STAGES = [
    "new_inquiry",
    "contacted",
    "counselling",
    "application_started",
    "documents_pending",
    "app_submitted",
    "offer_sent",
    "enrolled",
    "lost_deferred",
]

IN_PROGRESS_STAGES = [
    "contacted",
    "counselling",
    "application_started",
    "documents_pending",
    "app_submitted",
]


async def list_leads(
    db: AsyncSession,
    stage: Optional[str],
    source: Optional[str],
    counsellor_id: Optional[int],
    search: Optional[str],
    page: int,
    size: int,
    include_archived: bool,
) -> CrmLeadListResponse:
    repo = CrmLeadRepository(db)
    total = await repo.count(stage, source, counsellor_id, search, include_archived)
    leads = await repo.list_leads(stage, source, counsellor_id, search, include_archived, page, size)
    return CrmLeadListResponse(
        data=[CrmLeadSummary.model_validate(lead) for lead in leads],
        total=total,
    )


async def get_lead(db: AsyncSession, lead_id: int) -> CrmLeadOut:
    repo = CrmLeadRepository(db)
    lead = await repo.get(lead_id)
    if lead is None:
        raise NotFoundError(f"Lead {lead_id} not found")
    return CrmLeadOut.model_validate(lead)


async def create_lead(
    db: AsyncSession,
    payload: CrmLeadCreate,
    counsellor_id: Optional[int] = None,
    counsellor_name: Optional[str] = None,
) -> CrmLeadOut:
    repo = CrmLeadRepository(db)
    data = payload.model_dump()
    if counsellor_id is not None:
        data["assigned_counsellor_id"] = counsellor_id
        data["counsellor_name"] = counsellor_name
    lead = await repo.create(data)

    # Log creation activity
    activity_repo = CrmActivityRepository(db)
    await activity_repo.create(
        {
            "lead_id": lead.lead_id,
            "activity_type": "note",
            "content": f"Lead created from source: {lead.source}",
            "counsellor_id": counsellor_id,
            "counsellor_name": counsellor_name,
        }
    )
    # Re-fetch to include the new activity
    return await get_lead(db, lead.lead_id)


async def update_lead(
    db: AsyncSession,
    lead_id: int,
    payload: CrmLeadUpdate,
) -> CrmLeadOut:
    repo = CrmLeadRepository(db)
    lead = await repo.get(lead_id)
    if lead is None:
        raise NotFoundError(f"Lead {lead_id} not found")

    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    await repo.update(lead, data)
    return await get_lead(db, lead_id)


async def update_stage(
    db: AsyncSession,
    lead_id: int,
    stage: str,
    counsellor_id: Optional[int] = None,
    counsellor_name: Optional[str] = None,
) -> CrmLeadOut:
    repo = CrmLeadRepository(db)
    lead = await repo.get(lead_id)
    if lead is None:
        raise NotFoundError(f"Lead {lead_id} not found")

    old_stage = lead.stage
    await repo.update(lead, {"stage": stage})

    # Log the stage change
    activity_repo = CrmActivityRepository(db)
    await activity_repo.create(
        {
            "lead_id": lead_id,
            "activity_type": "stage_change",
            "content": f"Stage changed from '{old_stage}' to '{stage}'",
            "counsellor_id": counsellor_id,
            "counsellor_name": counsellor_name,
        }
    )
    return await get_lead(db, lead_id)


async def delete_lead(db: AsyncSession, lead_id: int) -> None:
    repo = CrmLeadRepository(db)
    lead = await repo.get(lead_id)
    if lead is None:
        raise NotFoundError(f"Lead {lead_id} not found")
    await repo.delete(lead)


async def get_pipeline(db: AsyncSession) -> CrmPipelineResponse:
    repo = CrmLeadRepository(db)
    all_leads = await repo.list_all_active()
    by_stage: dict[str, list[CrmLead]] = {s: [] for s in PIPELINE_STAGES}
    for lead in all_leads:
        if lead.stage in by_stage:
            by_stage[lead.stage].append(lead)
        else:
            by_stage.setdefault(lead.stage, []).append(lead)

    stages = [
        CrmPipelineStage(
            stage=stage,
            count=len(leads),
            leads=[CrmLeadSummary.model_validate(l) for l in leads],
        )
        for stage, leads in by_stage.items()
        if stage in PIPELINE_STAGES
    ]
    # Sort by canonical order
    stages.sort(key=lambda s: PIPELINE_STAGES.index(s.stage) if s.stage in PIPELINE_STAGES else 999)
    return CrmPipelineResponse(stages=stages)


async def get_dashboard(db: AsyncSession) -> CrmDashboardResponse:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    thirty_days_ago = now - timedelta(days=30)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    # new_leads_30d
    stmt_new = select(func.count()).where(CrmLead.created_at >= thirty_days_ago)
    new_leads_30d: int = (await db.execute(stmt_new)).scalar_one()

    # awaiting_contact
    stmt_await = select(func.count()).where(
        and_(CrmLead.stage == "new_inquiry", CrmLead.is_archived == False)  # noqa: E712
    )
    awaiting_contact: int = (await db.execute(stmt_await)).scalar_one()

    # in_progress
    stmt_prog = select(func.count()).where(
        and_(CrmLead.stage.in_(IN_PROGRESS_STAGES), CrmLead.is_archived == False)  # noqa: E712
    )
    in_progress: int = (await db.execute(stmt_prog)).scalar_one()

    # offers_sent
    stmt_offer = select(func.count()).where(CrmLead.stage == "offer_sent")
    offers_sent: int = (await db.execute(stmt_offer)).scalar_one()

    # enrolled_30d
    stmt_enr = select(func.count()).where(
        and_(CrmLead.stage == "enrolled", CrmLead.updated_at >= thirty_days_ago)
    )
    enrolled_30d: int = (await db.execute(stmt_enr)).scalar_one()

    # total for conversion rate
    stmt_total = select(func.count()).select_from(CrmLead)
    total_leads: int = (await db.execute(stmt_total)).scalar_one()
    stmt_enrolled_total = select(func.count()).where(CrmLead.stage == "enrolled")
    enrolled_total: int = (await db.execute(stmt_enrolled_total)).scalar_one()
    conversion_rate = round((enrolled_total / total_leads) * 100, 2) if total_leads > 0 else 0.0

    # followups_today count
    stmt_fup_count = select(func.count()).where(
        and_(
            CrmLead.followup_date >= today_start,
            CrmLead.followup_date < today_end,
            CrmLead.is_archived == False,  # noqa: E712
        )
    )
    followups_today_count: int = (await db.execute(stmt_fup_count)).scalar_one()

    # by_source
    repo = CrmLeadRepository(db)
    source_rows = await repo.source_counts()
    by_source = {row[0]: row[1] for row in source_rows}

    # pipeline_counts
    stage_rows = await repo.stage_counts()
    pipeline_counts = {row[0]: row[1] for row in stage_rows}

    # recent_leads (last 5)
    stmt_recent = (
        select(CrmLead).order_by(CrmLead.created_at.desc()).limit(5)
    )
    recent_leads_orm = list((await db.execute(stmt_recent)).scalars().all())
    recent_leads = [CrmLeadSummary.model_validate(l) for l in recent_leads_orm]

    # followups_today list
    stmt_fup = (
        select(CrmLead)
        .where(
            and_(
                CrmLead.followup_date >= today_start,
                CrmLead.followup_date < today_end,
                CrmLead.is_archived == False,  # noqa: E712
            )
        )
        .order_by(CrmLead.followup_date)
    )
    followups_today_orm = list((await db.execute(stmt_fup)).scalars().all())
    followups_today = [CrmLeadSummary.model_validate(l) for l in followups_today_orm]

    stats = CrmDashboardStats(
        new_leads_30d=new_leads_30d,
        awaiting_contact=awaiting_contact,
        in_progress=in_progress,
        offers_sent=offers_sent,
        enrolled_30d=enrolled_30d,
        conversion_rate=conversion_rate,
        followups_today=followups_today_count,
        by_source=by_source,
        pipeline_counts=pipeline_counts,
    )
    return CrmDashboardResponse(
        stats=stats,
        recent_leads=recent_leads,
        followups_today=followups_today,
    )


async def add_activity(
    db: AsyncSession,
    lead_id: int,
    payload: CrmActivityCreate,
    counsellor_id: Optional[int] = None,
    counsellor_name: Optional[str] = None,
) -> CrmActivityOut:
    # Ensure lead exists
    lead = await CrmLeadRepository(db).get(lead_id)
    if lead is None:
        raise NotFoundError(f"Lead {lead_id} not found")

    activity_repo = CrmActivityRepository(db)
    activity = await activity_repo.create(
        {
            "lead_id": lead_id,
            "activity_type": payload.activity_type,
            "content": payload.content,
            "counsellor_id": counsellor_id,
            "counsellor_name": counsellor_name,
        }
    )
    return CrmActivityOut.model_validate(activity)


async def export_leads_csv(
    db: AsyncSession,
    stage: Optional[str],
    source: Optional[str],
    export_format: Optional[str] = "template",
) -> str:
    repo = CrmLeadRepository(db)
    leads = await repo.list_export(stage, source)

    output = io.StringIO()
    writer = csv.writer(output)

    if export_format in ("template", "zoho", "excel"):
        # Exact Inspire College / Zoho CRM template matching the user's reference
        writer.writerow(
            [
                "Record Id",
                "Students Pipeline Owner.id",
                "Students Pipeline Owner",
                "Amount",
                "Students Pipeline Name",
                "Closing Date",
                "Account Name.id",
                "Account Name",
                "Stage",
                "Type",
                "Probability (%)",
                "Lead Source",
                "Created By.id",
                "Created By",
                "Modified By.id",
                "Modified By",
                "Created Time",
                "Modified Time",
                "Description",
                "Contact Name.id",
                "Contact Name",
                "Expected Revenue",
                "Last Activity Time",
                "Lead Conversion Time",
                "Sales Cycle Duration",
                "Overall Sales Duration",
                "Pipeline",
                "Change Log Time",
                "Locked",
                "Reason For Loss",
                "Social Lead ID",
                "Phone 1",
                "Email 1",
                "Phone",
                "Mobile",
                "Email",
                "School",
                "Nationality",
                "Faculty  (Schools)",
                "Selected Program",
                "Student Highest Education Qualification",
                "Student Status",
                "Parents Occupation",
                "Parents Email",
                "Address Line 1",
                "Address Line 2",
                "City",
                "Country",
            ]
        )
        stage_display = {
            "new_inquiry": "New Inquiry",
            "contacted": "Contacted",
            "counselling": "Counselling",
            "application_started": "Application Started",
            "documents_pending": "Documents Pending",
            "app_submitted": "App Submitted",
            "offer_sent": "Offer Sent",
            "enrolled": "Payment Done",
            "lost_deferred": "Not Interested / Declined",
        }
        for lead in leads:
            closing_date = (
                lead.followup_date.strftime("%Y-%m-%d") if lead.followup_date else ""
            )
            prob = 100 if lead.stage == "enrolled" else (90 if lead.stage == "offer_sent" else 50)
            writer.writerow(
                [
                    f"zcrm_{lead.lead_id}",
                    f"zcrm_{lead.assigned_counsellor_id or ''}",
                    lead.counsellor_name or "Inspire College",
                    lead.amount or "",
                    lead.full_name,
                    closing_date,
                    "",
                    "",
                    stage_display.get(lead.stage, lead.stage.replace("_", " ").title()),
                    "",
                    prob,
                    lead.source.replace("_", " ").title(),
                    "",
                    lead.counsellor_name or "Admissions Staff",
                    "",
                    lead.counsellor_name or "Inspire College",
                    lead.created_at.strftime("%Y-%m-%d %H:%M:%S") if lead.created_at else "",
                    lead.updated_at.strftime("%Y-%m-%d %H:%M:%S") if lead.updated_at else "",
                    lead.notes or lead.message or "",
                    "",
                    lead.full_name,
                    lead.amount or "",
                    lead.updated_at.strftime("%Y-%m-%d %H:%M:%S") if lead.updated_at else "",
                    0,
                    0,
                    0,
                    "InspireX Pipeline",
                    lead.updated_at.strftime("%Y-%m-%d %H:%M:%S") if lead.updated_at else "",
                    False,
                    "",
                    lead.social_lead_id or "",
                    lead.phone,
                    lead.email or "",
                    lead.phone,
                    lead.whatsapp or lead.phone,
                    lead.email or "",
                    lead.school or "",
                    lead.nationality or "Sri Lankan",
                    lead.faculty or "",
                    lead.interested_course or lead.interested_programme or "",
                    lead.highest_qualification or "",
                    lead.student_status or stage_display.get(lead.stage, ""),
                    lead.parents_occupation or "",
                    lead.parents_email or "",
                    lead.address_line1 or "",
                    lead.address_line2 or "",
                    lead.city or "",
                    lead.country or "Sri Lanka",
                ]
            )
    else:
        # Standard clean CSV
        writer.writerow(
            [
                "Lead ID",
                "Full Name",
                "Email",
                "Phone",
                "WhatsApp",
                "City",
                "District",
                "Programme",
                "Course",
                "Source",
                "Stage",
                "Priority",
                "Counsellor",
                "Notes",
                "Follow-up Date",
                "Follow-up Type",
                "Created At",
            ]
        )
        for lead in leads:
            writer.writerow(
                [
                    lead.lead_id,
                    lead.full_name,
                    lead.email or "",
                    lead.phone,
                    lead.whatsapp or "",
                    lead.city or "",
                    lead.district or "",
                    lead.interested_programme or "",
                    lead.interested_course or "",
                    lead.source,
                    lead.stage,
                    lead.priority,
                    lead.counsellor_name or "",
                    lead.notes or "",
                    lead.followup_date.isoformat() if lead.followup_date else "",
                    lead.followup_type or "",
                    lead.created_at.isoformat() if lead.created_at else "",
                ]
            )
    return output.getvalue()
