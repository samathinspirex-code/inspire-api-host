import csv
import io
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.modules.auth.models import AccessLevel, User, UserAccessLevel
from app.modules.crm.models.lead import CrmLead
from app.modules.crm.models.activity import CrmActivity
from app.modules.crm.models.counsellor_status import CrmCounsellorStatus
from app.modules.crm.models.assignment_settings import CrmAssignmentSettings
from app.modules.crm.repository import CrmActivityRepository, CrmLeadRepository
from app.modules.crm.schemas import (
    CrmActivityCreate,
    CrmActivityOut,
    CrmCounsellorLeadPreview,
    CrmAssignmentSettingsOut,
    CrmCounsellorOut,
    CrmCounsellorReportResponse,
    CrmCounsellorReportRow,
    CrmCounsellorRosterItem,
    CrmDashboardResponse,
    CrmDashboardStats,
    CrmLeadCreate,
    CrmLeadFilterOptions,
    CrmLeadListResponse,
    CrmLeadOut,
    CrmLeadSummary,
    CrmLeadUpdate,
    CrmPipelineResponse,
    CrmPipelineStage,
    CrmReportDailyPoint,
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


ACTIVE_ASSIGNED_STAGES = [
    "new_inquiry",
    "contacted",
    "counselling",
    "application_started",
    "documents_pending",
    "app_submitted",
    "offer_sent",
]


_counsellor_schema_ready = False
_awarding_body_column_ready = False


async def _crm_schema_present(db: AsyncSession) -> bool:
    """Single probe for every piece of CRM schema added after the base migration."""
    return bool(
        await db.scalar(
            text(
                "SELECT to_regclass('public.crm_counsellor_status') IS NOT NULL "
                "AND to_regclass('public.crm_assignment_settings') IS NOT NULL "
                "AND EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='crm_counsellor_status' "
                "AND column_name='assign_order') "
                "AND EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='crm_leads' "
                "AND column_name='awarding_body')"
            )
        )
    )


async def _ensure_counsellor_status_table(db: AsyncSession) -> None:
    global _counsellor_schema_ready, _awarding_body_column_ready
    if _counsellor_schema_ready:
        return
    if await _crm_schema_present(db):
        _counsellor_schema_ready = True
        _awarding_body_column_ready = True
        return
    await db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS crm_counsellor_status ("
            "user_id INTEGER PRIMARY KEY REFERENCES users(user_id), "
            "is_active BOOLEAN NOT NULL DEFAULT true, "
            "updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()"
            ")"
        )
    )
    await db.execute(
        text(
            "ALTER TABLE crm_counsellor_status "
            "ADD COLUMN IF NOT EXISTS assign_order INTEGER NOT NULL DEFAULT 0"
        )
    )
    await db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS crm_assignment_settings ("
            "id INTEGER PRIMARY KEY, "
            "auto_assign_enabled BOOLEAN NOT NULL DEFAULT true, "
            "last_counsellor_id INTEGER, "
            "last_assigned_on DATE, "
            "updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()"
            ")"
        )
    )
    await db.execute(
        text(
            "INSERT INTO crm_assignment_settings (id, auto_assign_enabled) "
            "VALUES (1, true) ON CONFLICT (id) DO NOTHING"
        )
    )
    await db.commit()
    _counsellor_schema_ready = True


def _counsellor_users_stmt(include_inactive: bool):
    stmt = (
        select(User, CrmCounsellorStatus)
        .join(UserAccessLevel, UserAccessLevel.user_id == User.user_id)
        .join(AccessLevel, AccessLevel.access_level_id == UserAccessLevel.access_level_id)
        .outerjoin(CrmCounsellorStatus, CrmCounsellorStatus.user_id == User.user_id)
        .where(
            User.is_active == True,  # noqa: E712
            AccessLevel.is_active == True,  # noqa: E712
            AccessLevel.access_key == "COUNSELLOR",
        )
        .order_by(func.coalesce(CrmCounsellorStatus.assign_order, 9999), User.full_name, User.email)
    )
    if not include_inactive:
        stmt = stmt.where(
            or_(CrmCounsellorStatus.is_active.is_(None), CrmCounsellorStatus.is_active.is_(True))
        )
    return stmt


def _dedupe_counsellor_rows(rows: list) -> list[tuple[User, CrmCounsellorStatus | None]]:
    seen: set[int] = set()
    unique_rows: list[tuple[User, CrmCounsellorStatus | None]] = []
    for user, status in rows:
        if user.user_id in seen:
            continue
        seen.add(user.user_id)
        unique_rows.append((user, status))
    return unique_rows


async def list_counsellors(db: AsyncSession) -> list[CrmCounsellorOut]:
    await _ensure_counsellor_status_table(db)
    rows = _dedupe_counsellor_rows((await db.execute(_counsellor_users_stmt(False))).all())
    return [
        CrmCounsellorOut(
            user_id=user.user_id,
            name=user.full_name or user.email,
            email=user.email,
            is_active=True,
        )
        for user, _status in rows
    ]


async def list_counsellor_roster(db: AsyncSession) -> list[CrmCounsellorRosterItem]:
    await _ensure_counsellor_status_table(db)
    rows = _dedupe_counsellor_rows((await db.execute(_counsellor_users_stmt(True))).all())
    roster: list[CrmCounsellorRosterItem] = []
    user_ids = [user.user_id for user, _status in rows]
    assigned_map: dict[int, int] = {}
    active_map: dict[int, int] = {}
    if user_ids:
        count_rows = (
            await db.execute(
                select(
                    CrmLead.assigned_counsellor_id,
                    func.count(),
                    func.count().filter(CrmLead.stage.in_(ACTIVE_ASSIGNED_STAGES)),
                )
                .where(
                    and_(
                        CrmLead.assigned_counsellor_id.in_(user_ids),
                        CrmLead.is_archived == False,  # noqa: E712
                    )
                )
                .group_by(CrmLead.assigned_counsellor_id)
            )
        ).all()
        assigned_map = {int(row[0]): int(row[1] or 0) for row in count_rows if row[0] is not None}
        active_map = {int(row[0]): int(row[2] or 0) for row in count_rows if row[0] is not None}
    preview_map: dict[int, list] = {user_id: [] for user_id in user_ids}
    if user_ids:
        preview_leads = list(
            (
                await db.execute(
                    select(CrmLead)
                    .where(
                        and_(
                            CrmLead.assigned_counsellor_id.in_(user_ids),
                            CrmLead.is_archived == False,  # noqa: E712
                            CrmLead.stage.in_(ACTIVE_ASSIGNED_STAGES),
                        )
                    )
                    .order_by(CrmLead.updated_at.desc())
                    .limit(max(8 * len(user_ids), 8))
                )
            ).scalars().all()
        )
        for lead in preview_leads:
            cid = lead.assigned_counsellor_id
            if cid is None or len(preview_map.get(cid, [])) >= 8:
                continue
            preview_map[cid].append(lead)
    for user, status in rows:
        preview = preview_map.get(user.user_id, [])
        roster.append(
            CrmCounsellorRosterItem(
                user_id=user.user_id,
                name=user.full_name or user.email,
                email=user.email,
                is_active=True if status is None else bool(status.is_active),
                assign_order=int(status.assign_order) if status is not None else 0,
                assigned_leads=assigned_map.get(user.user_id, 0),
                active_leads=active_map.get(user.user_id, 0),
                leads=[
                    CrmCounsellorLeadPreview(
                        lead_id=lead.lead_id,
                        full_name=lead.full_name,
                        phone=lead.phone,
                        stage=lead.stage,
                        interested_course=lead.interested_course,
                        interested_programme=lead.interested_programme,
                    )
                    for lead in preview
                ],
            )
        )
    roster.sort(key=lambda item: (not item.is_active, item.assign_order, item.name.lower()))
    return roster


async def set_counsellor_active(db: AsyncSession, user_id: int, is_active: bool) -> CrmCounsellorRosterItem:
    await _ensure_counsellor_status_table(db)
    users = _dedupe_counsellor_rows((await db.execute(_counsellor_users_stmt(True))).all())
    if not any(user.user_id == user_id for user, _status in users):
        raise NotFoundError("Counsellor not found")
    existing = await db.get(CrmCounsellorStatus, user_id)
    if existing is None:
        db.add(CrmCounsellorStatus(user_id=user_id, is_active=is_active))
    else:
        existing.is_active = is_active
    await db.commit()
    roster = await list_counsellor_roster(db)
    item = next((row for row in roster if row.user_id == user_id), None)
    if item is None:
        raise NotFoundError("Counsellor not found")
    return item


async def _registered_counsellor(db: AsyncSession, user_id: int) -> CrmCounsellorOut:
    counsellors = await list_counsellors(db)
    counsellor = next((item for item in counsellors if item.user_id == user_id), None)
    if counsellor is None:
        raise ValidationError("Select an active user with Admissions Counsellor access.")
    return counsellor


async def _actor_name(db: AsyncSession, user_id: int, email: str) -> str:
    name = await db.scalar(select(User.full_name).where(User.user_id == user_id))
    return name or email


async def _settings_row(db: AsyncSession, lock: bool = False) -> CrmAssignmentSettings:
    await _ensure_counsellor_status_table(db)
    stmt = select(CrmAssignmentSettings).where(CrmAssignmentSettings.id == 1)
    if lock:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = CrmAssignmentSettings(id=1, auto_assign_enabled=True)
        db.add(row)
        await db.flush()
        if lock:
            row = (
                await db.execute(
                    select(CrmAssignmentSettings).where(CrmAssignmentSettings.id == 1).with_for_update()
                )
            ).scalar_one()
    return row


async def _rotation_queue(db: AsyncSession) -> list[CrmCounsellorOut]:
    return await list_counsellors(db)


def _next_in_rotation(
    queue: list[CrmCounsellorOut],
    last_counsellor_id: Optional[int],
    last_assigned_on: Optional[date],
    today: date,
) -> Optional[CrmCounsellorOut]:
    if not queue:
        return None
    if last_assigned_on != today or last_counsellor_id is None:
        return queue[0]
    ids = [item.user_id for item in queue]
    try:
        return queue[(ids.index(last_counsellor_id) + 1) % len(queue)]
    except ValueError:
        return queue[0]


async def get_assignment_settings(db: AsyncSession) -> CrmAssignmentSettingsOut:
    settings = await _settings_row(db)
    queue = await _rotation_queue(db)
    nxt = (
        _next_in_rotation(
            queue,
            settings.last_counsellor_id,
            settings.last_assigned_on,
            datetime.now(timezone.utc).date(),
        )
        if settings.auto_assign_enabled
        else None
    )
    return CrmAssignmentSettingsOut(
        auto_assign_enabled=settings.auto_assign_enabled,
        last_counsellor_id=settings.last_counsellor_id,
        last_assigned_on=settings.last_assigned_on.isoformat() if settings.last_assigned_on else None,
        next_counsellor_id=nxt.user_id if nxt else None,
        next_counsellor_name=nxt.name if nxt else None,
        rotation=queue,
    )


async def update_assignment_settings(db: AsyncSession, enabled: bool) -> CrmAssignmentSettingsOut:
    settings = await _settings_row(db)
    settings.auto_assign_enabled = enabled
    await db.commit()
    return await get_assignment_settings(db)


async def set_counsellor_order(db: AsyncSession, user_ids: list[int]) -> list[CrmCounsellorRosterItem]:
    await _ensure_counsellor_status_table(db)
    known = {user.user_id for user, _status in _dedupe_counsellor_rows((await db.execute(_counsellor_users_stmt(True))).all())}
    for index, user_id in enumerate(user_ids):
        if user_id not in known:
            continue
        existing = await db.get(CrmCounsellorStatus, user_id)
        if existing is None:
            db.add(CrmCounsellorStatus(user_id=user_id, is_active=True, assign_order=index))
        else:
            existing.assign_order = index
    await db.commit()
    return await list_counsellor_roster(db)


async def apply_auto_assignment(db: AsyncSession, lead_id: int, *, commit: bool = False) -> Optional[CrmCounsellorOut]:
    settings = await _settings_row(db, lock=True)
    if not settings.auto_assign_enabled:
        return None
    queue = await _rotation_queue(db)
    today = datetime.now(timezone.utc).date()
    nxt = _next_in_rotation(queue, settings.last_counsellor_id, settings.last_assigned_on, today)
    if nxt is None:
        return None
    settings.last_counsellor_id = nxt.user_id
    settings.last_assigned_on = today
    await db.execute(
        text(
            "UPDATE crm_leads SET assigned_counsellor_id = :cid, counsellor_name = :name "
            "WHERE lead_id = :lead_id"
        ),
        {"cid": nxt.user_id, "name": nxt.name, "lead_id": lead_id},
    )
    if commit:
        await db.commit()
    else:
        await db.flush()
    return nxt


AWARDING_BODY_HINTS = (
    ("jain", "Jain University"),
    ("lsbf", "LSBF"),
    ("winc", "WINC"),
    ("bedford", "University of Bedfordshire"),
    ("uob", "University of Bedfordshire"),
    ("cpd", "CPD"),
    ("finance lit", "CPD"),
    ("finance literacy", "CPD"),
    ("digital marketing", "CPD"),
    ("ai mastery", "CPD"),
    ("athe", "ATHE"),
    ("hnd", "ATHE"),
    ("foundation", "ATHE"),
)


async def _ensure_awarding_body_column(db: AsyncSession, *, backfill: bool = False) -> None:
    """Confirm the awarding_body column exists. Backfill only runs from scripts."""
    global _awarding_body_column_ready
    if _awarding_body_column_ready:
        return
    if await _crm_schema_present(db):
        _awarding_body_column_ready = True
        return
    await db.execute(
        text("ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS awarding_body VARCHAR(100)")
    )
    await db.commit()
    _awarding_body_column_ready = True


async def backfill_awarding_bodies(db: AsyncSession) -> None:
    """Derive awarding_body from the catalogue, then from course-name hints."""
    await _ensure_awarding_body_column(db)
    has_courses = await db.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='academic_courses')"
        )
    )
    if has_courses:
        await db.execute(
            text(
                """
                UPDATE crm_leads AS lead
                SET awarding_body = course.awarding_body
                FROM academic_courses AS course
                WHERE lead.awarding_body IS NULL
                  AND course.awarding_body IS NOT NULL
                  AND (
                    lower(lead.interested_course) = lower(course.title)
                    OR lower(lead.interested_programme) = lower(course.title)
                  )
                """
            )
        )
        await db.execute(
            text(
                """
                UPDATE crm_leads AS lead
                SET awarding_body = mapped.awarding_body
                FROM (
                    SELECT programme.name, MIN(course.awarding_body) AS awarding_body
                    FROM academic_programmes AS programme
                    JOIN academic_courses AS course ON course.programme_id = programme.programme_id
                    WHERE course.awarding_body IS NOT NULL AND course.awarding_body <> ''
                    GROUP BY programme.name
                ) AS mapped
                WHERE lead.awarding_body IS NULL
                  AND lower(lead.interested_programme) = lower(mapped.name)
                """
            )
        )
    # Earlier hints win, so each lead is matched against the whole list at once
    # and the lowest-priority match is applied.
    values = ", ".join(f"(:p{i}, :b{i}, {i})" for i in range(len(AWARDING_BODY_HINTS)))
    params: dict[str, str] = {}
    for index, (hint, body) in enumerate(AWARDING_BODY_HINTS):
        params[f"p{index}"] = f"%{hint}%"
        params[f"b{index}"] = body
    await db.execute(
        text(
            f"""
            UPDATE crm_leads AS lead
            SET awarding_body = pick.body
            FROM (
                SELECT lead_id, body FROM (
                    SELECT
                        l.lead_id,
                        h.body,
                        row_number() OVER (PARTITION BY l.lead_id ORDER BY h.prio) AS rn
                    FROM crm_leads AS l
                    JOIN (VALUES {values}) AS h(pattern, body, prio)
                      ON lower(coalesce(l.interested_course, '')) LIKE h.pattern
                      OR lower(coalesce(l.interested_programme, '')) LIKE h.pattern
                    WHERE l.awarding_body IS NULL
                ) AS ranked
                WHERE rn = 1
            ) AS pick
            WHERE lead.lead_id = pick.lead_id
            """
        ),
        params,
    )
    await db.commit()


async def list_leads(
    db: AsyncSession,
    stage: Optional[str],
    source: Optional[str],
    counsellor_id: Optional[int],
    search: Optional[str],
    page: int,
    size: int,
    include_archived: bool,
    awarding_body: Optional[str] = None,
    programme: Optional[str] = None,
    unassigned: bool = False,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
) -> CrmLeadListResponse:
    await _ensure_awarding_body_column(db)
    leads, total = await CrmLeadRepository(db).list_with_total(
        stage, source, counsellor_id, search, include_archived, page, size, awarding_body, programme, unassigned,
        created_from, created_to,
    )
    return CrmLeadListResponse(
        data=[CrmLeadSummary.model_validate(lead) for lead in leads],
        total=total,
    )


async def list_lead_filters(db: AsyncSession) -> CrmLeadFilterOptions:
    await _ensure_awarding_body_column(db)
    programme_expr = func.coalesce(CrmLead.interested_programme, CrmLead.interested_course)
    rows = (
        await db.execute(
            select(CrmLead.awarding_body, programme_expr)
            .where(CrmLead.is_archived == False)  # noqa: E712
            .distinct()
        )
    ).all()
    bodies: set[str] = set()
    programmes: set[str] = set()
    for body, programme in rows:
        if body:
            bodies.add(body)
        if programme:
            programmes.add(programme)
    return CrmLeadFilterOptions(
        awarding_bodies=sorted(bodies),
        programmes=sorted(programmes)[:200],
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
    await _ensure_awarding_body_column(db)
    repo = CrmLeadRepository(db)
    data = payload.model_dump()
    explicit_id = data.pop("assigned_counsellor_id", None)
    assignment_note = ""
    if explicit_id is not None:
        counsellor = await _registered_counsellor(db, explicit_id)
        data["assigned_counsellor_id"] = counsellor.user_id
        data["counsellor_name"] = counsellor.name
        assignment_note = f" Manually assigned to {counsellor.name}."
    else:
        data["assigned_counsellor_id"] = None
        data["counsellor_name"] = None
    lead = await repo.create(data)

    if explicit_id is None:
        picked = await apply_auto_assignment(db, lead.lead_id, commit=True)
        if picked:
            assignment_note = f" Auto-assigned to {picked.name} (daily rotation)."

    activity_repo = CrmActivityRepository(db)
    await activity_repo.create(
        {
            "lead_id": lead.lead_id,
            "activity_type": "note",
            "content": f"Lead created from source: {lead.source}.{assignment_note}",
            "counsellor_id": counsellor_id,
            "counsellor_name": counsellor_name,
        }
    )
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

    data = payload.model_dump(include=payload.model_fields_set)
    if "assigned_counsellor_id" in data:
        counsellor_id = data["assigned_counsellor_id"]
        if counsellor_id is None:
            data["counsellor_name"] = None
        else:
            counsellor = await _registered_counsellor(db, counsellor_id)
            data["counsellor_name"] = counsellor.name
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


PIPELINE_PREVIEW_MAX = 20


async def get_pipeline(
    db: AsyncSession,
    preview_per_stage: int = PIPELINE_PREVIEW_MAX,
    counsellor_id: Optional[int] = None,
    awarding_body: Optional[str] = None,
    programme: Optional[str] = None,
    unassigned: bool = False,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
) -> CrmPipelineResponse:
    await _ensure_awarding_body_column(db)
    limit = max(1, min(preview_per_stage, PIPELINE_PREVIEW_MAX))
    previews, counts = await CrmLeadRepository(db).pipeline_snapshot(
        PIPELINE_STAGES, limit, counsellor_id, awarding_body, programme, unassigned, created_from, created_to
    )
    return CrmPipelineResponse(
        stages=[
            CrmPipelineStage(
                stage=stage,
                count=counts.get(stage, 0),
                leads=[CrmLeadSummary.model_validate(lead) for lead in previews.get(stage, [])],
            )
            for stage in PIPELINE_STAGES
        ]
    )


async def get_dashboard(db: AsyncSession) -> CrmDashboardResponse:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    thirty_days_ago = now - timedelta(days=30)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    active = CrmLead.is_archived == False  # noqa: E712

    totals = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(CrmLead.created_at >= thirty_days_ago),
                func.count().filter(CrmLead.stage == "new_inquiry"),
                func.count().filter(CrmLead.stage.in_(IN_PROGRESS_STAGES)),
                func.count().filter(CrmLead.stage == "offer_sent"),
                func.count().filter(and_(CrmLead.stage == "enrolled", CrmLead.updated_at >= thirty_days_ago)),
                func.count().filter(CrmLead.stage == "enrolled"),
                func.count().filter(and_(CrmLead.followup_date >= today_start, CrmLead.followup_date < today_end)),
                func.count().filter(
                    and_(
                        CrmLead.assigned_counsellor_id.is_(None),
                        or_(CrmLead.counsellor_name.is_(None), CrmLead.counsellor_name == ""),
                    )
                ),
            ).where(active)
        )
    ).one()
    (
        total_leads,
        new_leads_30d,
        awaiting_contact,
        in_progress,
        offers_sent,
        enrolled_30d,
        enrolled_total,
        followups_today_count,
        unassigned,
    ) = (int(value or 0) for value in totals)
    conversion_rate = round((enrolled_total / total_leads) * 100, 2) if total_leads > 0 else 0.0

    programme_name = func.coalesce(
        func.nullif(CrmLead.interested_course, ""),
        func.nullif(CrmLead.interested_programme, ""),
        "Unspecified",
    )
    mix_rows = (
        await db.execute(
            select(CrmLead.stage, CrmLead.source, programme_name, func.count())
            .where(active)
            .group_by(CrmLead.stage, CrmLead.source, programme_name)
        )
    ).all()
    by_source: dict[str, int] = {}
    by_programme: dict[str, int] = {}
    pipeline_counts: dict[str, int] = {}
    for stage, source, programme, cnt in mix_rows:
        value = int(cnt or 0)
        if stage:
            pipeline_counts[stage] = pipeline_counts.get(stage, 0) + value
        if source:
            by_source[source] = by_source.get(source, 0) + value
        if programme:
            by_programme[programme] = by_programme.get(programme, 0) + value

    recent_leads = [
        CrmLeadSummary.model_validate(lead)
        for lead in (
            await db.execute(
                select(CrmLead).where(active).order_by(CrmLead.created_at.desc()).limit(5)
            )
        ).scalars().all()
    ]
    followups_today = [
        CrmLeadSummary.model_validate(lead)
        for lead in (
            await db.execute(
                select(CrmLead)
                .where(and_(active, CrmLead.followup_date >= today_start, CrmLead.followup_date < today_end))
                .order_by(CrmLead.followup_date)
            )
        ).scalars().all()
    ]

    return CrmDashboardResponse(
        stats=CrmDashboardStats(
            total_leads=total_leads,
            new_leads_30d=new_leads_30d,
            awaiting_contact=awaiting_contact,
            in_progress=in_progress,
            offers_sent=offers_sent,
            enrolled_30d=enrolled_30d,
            conversion_rate=conversion_rate,
            followups_today=followups_today_count,
            unassigned=unassigned,
            by_source=by_source,
            by_programme=by_programme,
            pipeline_counts=pipeline_counts,
        ),
        recent_leads=recent_leads,
        followups_today=followups_today,
    )


def _period_bounds(
    date_from: Optional[date], date_to: Optional[date]
) -> tuple[datetime, datetime, date, date]:
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    start_d = date_from or today
    end_d = date_to or today
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    start = datetime.combine(start_d, time.min)
    end = datetime.combine(end_d + timedelta(days=1), time.min)
    return start, end, start_d, end_d


def _conversion_rate(enrolled: int, new_leads: int) -> float:
    if new_leads <= 0:
        return 0.0
    return round((enrolled / new_leads) * 100, 1)


def _count_map(rows: list) -> dict[Optional[int], int]:
    return {row[0]: int(row[1] or 0) for row in rows}


def _day_key(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


async def get_followups(db: AsyncSession) -> list[CrmLeadSummary]:
    today_start = datetime.combine(datetime.now(timezone.utc).date(), time.min)
    leads = (
        await db.execute(
            select(CrmLead)
            .where(
                CrmLead.is_archived == False,  # noqa: E712
                CrmLead.followup_date >= today_start,
            )
            .order_by(CrmLead.followup_date)
        )
    ).scalars().all()
    return [CrmLeadSummary.model_validate(lead) for lead in leads]


async def get_counsellor_report(
    db: AsyncSession,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    counsellor_id: Optional[int] = None,
) -> CrmCounsellorReportResponse:
    start, end, start_d, end_d = _period_bounds(date_from, date_to)
    name_rows = (
        await db.execute(
            select(User.user_id, User.full_name, User.email)
            .join(UserAccessLevel, UserAccessLevel.user_id == User.user_id)
            .join(AccessLevel, AccessLevel.access_level_id == UserAccessLevel.access_level_id)
            .where(
                User.is_active == True,  # noqa: E712
                AccessLevel.is_active == True,  # noqa: E712
                AccessLevel.access_key == "COUNSELLOR",
            )
        )
    ).all()
    names = {int(row[0]): (row[1] or row[2] or f"Counsellor {row[0]}") for row in name_rows}

    lead_scope = [CrmLead.is_archived == False]  # noqa: E712
    if counsellor_id is not None:
        lead_scope.append(CrmLead.assigned_counsellor_id == counsellor_id)

    in_created = and_(CrmLead.created_at >= start, CrmLead.created_at < end)
    in_updated = and_(CrmLead.updated_at >= start, CrmLead.updated_at < end)
    in_follow = and_(CrmLead.followup_date >= start, CrmLead.followup_date < end)
    metric_rows = (
        await db.execute(
            select(
                CrmLead.assigned_counsellor_id,
                func.count().filter(in_created),
                func.count().filter(and_(in_updated, CrmLead.stage == "offer_sent")),
                func.count().filter(and_(in_updated, CrmLead.stage == "enrolled")),
                func.count().filter(and_(in_updated, CrmLead.stage == "lost_deferred")),
                func.count().filter(in_follow),
                func.count(),
            )
            .where(and_(*lead_scope))
            .group_by(CrmLead.assigned_counsellor_id)
        )
    ).all()
    new_map: dict[Optional[int], int] = {}
    offers_map: dict[Optional[int], int] = {}
    enrolled_map: dict[Optional[int], int] = {}
    lost_map: dict[Optional[int], int] = {}
    follow_map: dict[Optional[int], int] = {}
    current_assigned: dict[int, int] = {}
    for cid, new_leads, offers, enrolled, lost, followups, assigned in metric_rows:
        new_map[cid] = int(new_leads or 0)
        offers_map[cid] = int(offers or 0)
        enrolled_map[cid] = int(enrolled or 0)
        lost_map[cid] = int(lost or 0)
        follow_map[cid] = int(followups or 0)
        if cid is not None:
            current_assigned[int(cid)] = int(assigned or 0)

    activity_filters = [CrmActivity.created_at >= start, CrmActivity.created_at < end]
    if counsellor_id is not None:
        activity_filters.append(CrmActivity.counsellor_id == counsellor_id)
    activity_day = func.date(CrmActivity.created_at)
    activity_rows = (
        await db.execute(
            select(CrmActivity.counsellor_id, activity_day, func.count())
            .where(and_(*activity_filters))
            .group_by(CrmActivity.counsellor_id, activity_day)
        )
    ).all()
    activity_map: dict[Optional[int], int] = {}
    daily_activities: dict[str, int] = {}
    for cid, day, cnt in activity_rows:
        value = int(cnt or 0)
        activity_map[cid] = activity_map.get(cid, 0) + value
        if day:
            key = _day_key(day)
            daily_activities[key] = daily_activities.get(key, 0) + value

    created_day = func.date(CrmLead.created_at)
    mix_rows = (
        await db.execute(
            select(CrmLead.stage, CrmLead.source, CrmLead.awarding_body, created_day, func.count())
            .where(and_(*lead_scope, in_created))
            .group_by(CrmLead.stage, CrmLead.source, CrmLead.awarding_body, created_day)
        )
    ).all()
    by_stage: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_awarding_body: dict[str, int] = {}
    daily_new: dict[str, int] = {}
    for stage, source, body, day, cnt in mix_rows:
        value = int(cnt or 0)
        if stage:
            by_stage[stage] = by_stage.get(stage, 0) + value
        if source:
            by_source[source] = by_source.get(source, 0) + value
        by_awarding_body[body or "Unspecified"] = by_awarding_body.get(body or "Unspecified", 0) + value
        if day:
            key = _day_key(day)
            daily_new[key] = daily_new.get(key, 0) + value

    enrolled_day = func.date(CrmLead.updated_at)
    daily_enrolled = {
        _day_key(row[0]): int(row[1] or 0)
        for row in (
            await db.execute(
                select(enrolled_day, func.count())
                .where(and_(*lead_scope, CrmLead.stage == "enrolled", in_updated))
                .group_by(enrolled_day)
            )
        ).all()
        if row[0]
    }

    daily: list[CrmReportDailyPoint] = []
    cursor = start_d
    while cursor <= end_d:
        key = cursor.isoformat()
        daily.append(
            CrmReportDailyPoint(
                date=key,
                new_leads=daily_new.get(key, 0),
                enrolled=daily_enrolled.get(key, 0),
                activities=daily_activities.get(key, 0),
            )
        )
        cursor += timedelta(days=1)

    ids = set(names)
    ids.update(k for k in new_map if k is not None)
    ids.update(k for k in activity_map if k is not None)
    ids.update(k for k in enrolled_map if k is not None)
    ids.update(k for k in offers_map if k is not None)
    if counsellor_id is not None:
        ids = {counsellor_id}

    rows: list[CrmCounsellorReportRow] = []
    for cid in sorted(ids, key=lambda item: names.get(item, f"Counsellor {item}")):
        new_leads = new_map.get(cid, 0)
        enrolled = enrolled_map.get(cid, 0)
        rows.append(
            CrmCounsellorReportRow(
                counsellor_id=cid,
                counsellor_name=names.get(cid, f"Counsellor {cid}"),
                new_leads=new_leads,
                activities=activity_map.get(cid, 0),
                offers=offers_map.get(cid, 0),
                enrolled=enrolled,
                lost=lost_map.get(cid, 0),
                followups=follow_map.get(cid, 0),
                current_assigned=current_assigned.get(cid, 0),
                conversion_rate=_conversion_rate(enrolled, new_leads),
            )
        )
    if counsellor_id is None and (new_map.get(None) or activity_map.get(None)):
        rows.append(
            CrmCounsellorReportRow(
                counsellor_id=None,
                counsellor_name="Unassigned",
                new_leads=new_map.get(None, 0),
                activities=activity_map.get(None, 0),
                offers=offers_map.get(None, 0),
                enrolled=enrolled_map.get(None, 0),
                lost=lost_map.get(None, 0),
                followups=follow_map.get(None, 0),
                current_assigned=0,
                conversion_rate=_conversion_rate(enrolled_map.get(None, 0), new_map.get(None, 0)),
            )
        )
    rows.sort(key=lambda row: (row.new_leads, row.enrolled, row.activities), reverse=True)

    totals = CrmCounsellorReportRow(
        counsellor_name="All counsellors" if counsellor_id is None else rows[0].counsellor_name if rows else "Counsellor",
        new_leads=sum(row.new_leads for row in rows),
        activities=sum(row.activities for row in rows),
        offers=sum(row.offers for row in rows),
        enrolled=sum(row.enrolled for row in rows),
        lost=sum(row.lost for row in rows),
        followups=sum(row.followups for row in rows),
        current_assigned=sum(row.current_assigned for row in rows),
        conversion_rate=_conversion_rate(sum(row.enrolled for row in rows), sum(row.new_leads for row in rows)),
        by_stage=by_stage,
    )
    return CrmCounsellorReportResponse(
        date_from=start_d.isoformat(),
        date_to=end_d.isoformat(),
        counsellor_id=counsellor_id,
        totals=totals,
        counsellors=rows,
        by_stage=by_stage,
        by_source=by_source,
        by_awarding_body=by_awarding_body,
        daily=daily,
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
    counsellor_id: Optional[int] = None,
    awarding_body: Optional[str] = None,
    programme: Optional[str] = None,
    unassigned: bool = False,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
) -> str:
    repo = CrmLeadRepository(db)
    leads = await repo.list_export(
        stage, source, counsellor_id, awarding_body, programme, unassigned,
        created_from, created_to,
    )

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
                    getattr(lead, "external_record_id", None) or f"zcrm_{lead.lead_id}",
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
