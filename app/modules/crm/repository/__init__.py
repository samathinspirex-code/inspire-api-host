from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, load_only, selectinload

from app.modules.crm.models.lead import CrmLead
from app.modules.crm.models.activity import CrmActivity


class CrmLeadRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _build_filters(
        self,
        stage: Optional[str],
        source: Optional[str],
        counsellor_id: Optional[int],
        search: Optional[str],
        include_archived: bool,
        awarding_body: Optional[str] = None,
        programme: Optional[str] = None,
        unassigned: bool = False,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> list[Any]:
        filters: list[Any] = []
        if not include_archived:
            filters.append(CrmLead.is_archived == False)  # noqa: E712
        if stage:
            filters.append(CrmLead.stage == stage)
        if source:
            filters.append(CrmLead.source == source)
        if unassigned:
            filters.append(CrmLead.assigned_counsellor_id.is_(None))
        elif counsellor_id is not None:
            filters.append(CrmLead.assigned_counsellor_id == counsellor_id)
        if awarding_body:
            filters.append(CrmLead.awarding_body == awarding_body)
        if programme:
            filters.append(
                or_(
                    CrmLead.interested_programme == programme,
                    CrmLead.interested_course == programme,
                )
            )
        if search:
            pattern = f"%{search}%"
            filters.append(
                or_(
                    CrmLead.full_name.ilike(pattern),
                    CrmLead.email.ilike(pattern),
                    CrmLead.phone.ilike(pattern),
                    CrmLead.city.ilike(pattern),
                    CrmLead.interested_programme.ilike(pattern),
                    CrmLead.interested_course.ilike(pattern),
                    CrmLead.awarding_body.ilike(pattern),
                    CrmLead.counsellor_name.ilike(pattern),
                )
            )
        if created_from:
            filters.append(CrmLead.created_at >= created_from)
        if created_to:
            filters.append(CrmLead.created_at < created_to)
        extra = extra or {}
        if extra.get("priority") in ("high", "medium", "low"):
            filters.append(CrmLead.priority == extra["priority"])
        for field in ("city", "country", "intake"):
            if extra.get(field):
                filters.append(getattr(CrmLead, field).ilike(str(extra[field])))
        for field in ("updated_at", "last_contacted_at", "followup_date"):
            if extra.get(f"{field}_from"):
                filters.append(getattr(CrmLead, field) >= extra[f"{field}_from"])
            if extra.get(f"{field}_to"):
                filters.append(getattr(CrmLead, field) < extra[f"{field}_to"])
        if extra.get("days_since_contact") is not None:
            cutoff = datetime.utcnow() - timedelta(days=int(extra["days_since_contact"]))
            filters.append(or_(CrmLead.last_contacted_at.is_(None), CrmLead.last_contacted_at < cutoff))
        if extra.get("followup_state") == "missing":
            filters.append(CrmLead.followup_date.is_(None))
        elif extra.get("followup_state") == "overdue":
            filters.append(CrmLead.followup_date < datetime.utcnow())
        elif extra.get("followup_state") == "due_today":
            start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
            filters.extend((CrmLead.followup_date >= start, CrmLead.followup_date < start + timedelta(days=1)))
        return filters

    async def count(
        self,
        stage: Optional[str],
        source: Optional[str],
        counsellor_id: Optional[int],
        search: Optional[str],
        include_archived: bool,
        awarding_body: Optional[str] = None,
        programme: Optional[str] = None,
        unassigned: bool = False,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> int:
        filters = self._build_filters(
            stage, source, counsellor_id, search, include_archived, awarding_body, programme, unassigned,
            created_from, created_to, extra,
        )
        stmt = select(func.count()).select_from(select(CrmLead).where(*filters).subquery())
        return (await self.db.execute(stmt)).scalar_one()

    async def list_leads(
        self,
        stage: Optional[str],
        source: Optional[str],
        counsellor_id: Optional[int],
        search: Optional[str],
        include_archived: bool,
        page: int,
        size: int,
        awarding_body: Optional[str] = None,
        programme: Optional[str] = None,
        unassigned: bool = False,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
    ) -> list[CrmLead]:
        filters = self._build_filters(
            stage, source, counsellor_id, search, include_archived, awarding_body, programme, unassigned,
            created_from, created_to,
        )
        stmt = (
            select(CrmLead)
            .where(*filters)
            .order_by(CrmLead.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_with_total(
        self,
        stage: Optional[str],
        source: Optional[str],
        counsellor_id: Optional[int],
        search: Optional[str],
        include_archived: bool,
        page: int,
        size: int,
        awarding_body: Optional[str] = None,
        programme: Optional[str] = None,
        unassigned: bool = False,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> tuple[list[CrmLead], int]:
        """One round trip for a page of leads plus the unpaginated total."""
        filters = self._build_filters(
            stage, source, counsellor_id, search, include_archived, awarding_body, programme, unassigned,
            created_from, created_to, extra,
        )
        sort = (extra or {}).get("sort", "newest")
        if sort == "oldest":
            order = (CrmLead.created_at.asc(), CrmLead.lead_id.asc())
        elif sort == "updated":
            order = (CrmLead.updated_at.desc(), CrmLead.lead_id.desc())
        elif sort == "priority":
            priority_order = case((CrmLead.priority == "high", 0), (CrmLead.priority == "medium", 1), (CrmLead.priority == "low", 2), else_=3)
            order = (priority_order, CrmLead.created_at.desc(), CrmLead.lead_id.desc())
        else:
            order = (CrmLead.created_at.desc(), CrmLead.lead_id.desc())
        stmt = (
            select(CrmLead, func.count().over().label("total"))
            .where(*filters)
            .order_by(*order)
            .offset((page - 1) * size)
            .limit(size)
        )
        rows = (await self.db.execute(stmt)).all()
        if not rows:
            total = await self.count(
                stage, source, counsellor_id, search, include_archived, awarding_body, programme, unassigned,
                created_from, created_to, extra,
            )
            return [], total
        return [row[0] for row in rows], int(rows[0][1] or 0)

    async def pipeline_snapshot(
        self,
        stages: list[str],
        limit: int,
        counsellor_id: Optional[int] = None,
        awarding_body: Optional[str] = None,
        programme: Optional[str] = None,
        unassigned: bool = False,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
    ) -> tuple[dict[str, list[CrmLead]], dict[str, int]]:
        """One round trip for every stage's preview cards and total count."""
        filters = self._build_filters(
            None, None, counsellor_id, None, False, awarding_body, programme, unassigned,
            created_from, created_to,
        )
        ranked = (
            select(
                CrmLead,
                func.row_number()
                .over(partition_by=CrmLead.stage, order_by=CrmLead.updated_at.desc())
                .label("rn"),
                func.count().over(partition_by=CrmLead.stage).label("stage_total"),
            )
            .where(*filters, CrmLead.stage.in_(stages))
            .subquery()
        )
        lead = aliased(CrmLead, ranked)
        rows = (
            await self.db.execute(
                select(lead, ranked.c.stage_total).where(ranked.c.rn <= limit).order_by(ranked.c.rn)
            )
        ).all()
        previews: dict[str, list[CrmLead]] = {stage: [] for stage in stages}
        counts: dict[str, int] = {stage: 0 for stage in stages}
        for row_lead, stage_total in rows:
            previews.setdefault(row_lead.stage, []).append(row_lead)
            counts[row_lead.stage] = int(stage_total or 0)
        return previews, counts

    async def list_pipeline_preview(self, stage: str, limit: int) -> list[CrmLead]:
        stmt = (
            select(CrmLead)
            .options(
                load_only(
                    CrmLead.lead_id,
                    CrmLead.full_name,
                    CrmLead.email,
                    CrmLead.phone,
                    CrmLead.city,
                    CrmLead.interested_programme,
                    CrmLead.interested_course,
                    CrmLead.awarding_body,
                    CrmLead.source,
                    CrmLead.stage,
                    CrmLead.priority,
                    CrmLead.amount,
                    CrmLead.counsellor_name,
                    CrmLead.assigned_counsellor_id,
                    CrmLead.followup_date,
                    CrmLead.followup_type,
                    CrmLead.created_at,
                    CrmLead.updated_at,
                )
            )
            .where(and_(CrmLead.stage == stage, CrmLead.is_archived == False))  # noqa: E712
            .order_by(CrmLead.updated_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get(self, lead_id: int) -> Optional[CrmLead]:
        stmt = (
            select(CrmLead)
            .where(CrmLead.lead_id == lead_id)
            .options(selectinload(CrmLead.activities))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def create(self, data: dict[str, Any], commit: bool = True) -> CrmLead:
        lead = CrmLead(**data)
        self.db.add(lead)
        await self.db.flush()
        if commit:
            await self.db.commit()
        await self.db.refresh(lead)
        # Re-fetch with activities loaded
        return await self.get(lead.lead_id)  # type: ignore[return-value]

    async def update(self, lead: CrmLead, data: dict[str, Any], commit: bool = True) -> CrmLead:
        for field, value in data.items():
            setattr(lead, field, value)
        await self.db.flush()
        if commit:
            await self.db.commit()
        await self.db.refresh(lead)
        return await self.get(lead.lead_id)  # type: ignore[return-value]

    async def delete(self, lead: CrmLead) -> None:
        await self.db.delete(lead)
        await self.db.commit()

    async def list_all_active(self) -> list[CrmLead]:
        stmt = (
            select(CrmLead)
            .where(CrmLead.is_archived == False)  # noqa: E712
            .order_by(CrmLead.stage, CrmLead.created_at.desc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_by_stage(self, stage: str) -> list[CrmLead]:
        stmt = (
            select(CrmLead)
            .where(and_(CrmLead.stage == stage, CrmLead.is_archived == False))  # noqa: E712
            .order_by(CrmLead.created_at.desc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def source_counts(self) -> list[Any]:
        stmt = (
            select(CrmLead.source, func.count().label("cnt"))
            .where(CrmLead.is_archived == False)  # noqa: E712
            .group_by(CrmLead.source)
        )
        return list((await self.db.execute(stmt)).all())

    async def stage_counts(self) -> list[Any]:
        stmt = (
            select(CrmLead.stage, func.count().label("cnt"))
            .where(CrmLead.is_archived == False)  # noqa: E712
            .group_by(CrmLead.stage)
        )
        return list((await self.db.execute(stmt)).all())

    async def list_export(
        self, stage: Optional[str], source: Optional[str],
        counsellor_id: Optional[int] = None, awarding_body: Optional[str] = None,
        programme: Optional[str] = None, unassigned: bool = False,
        created_from: Optional[datetime] = None, created_to: Optional[datetime] = None,
        priority: Optional[str] = None,
        search: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> list[CrmLead]:
        filters_extra = {**(extra or {}), "priority": priority}
        filters = self._build_filters(
            stage, source, counsellor_id, search, False, awarding_body,
            programme, unassigned, created_from, created_to, filters_extra,
        )
        stmt = select(CrmLead).where(*filters).order_by(CrmLead.created_at.desc())
        return list((await self.db.execute(stmt)).scalars().all())


class CrmActivityRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict[str, Any], commit: bool = True) -> CrmActivity:
        activity = CrmActivity(**data)
        self.db.add(activity)
        await self.db.flush()
        if commit:
            await self.db.commit()
        await self.db.refresh(activity)
        return activity
