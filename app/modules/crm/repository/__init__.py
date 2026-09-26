from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    ) -> list[Any]:
        filters: list[Any] = []
        if not include_archived:
            filters.append(CrmLead.is_archived == False)  # noqa: E712
        if stage:
            filters.append(CrmLead.stage == stage)
        if source:
            filters.append(CrmLead.source == source)
        if counsellor_id is not None:
            filters.append(CrmLead.assigned_counsellor_id == counsellor_id)
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
                )
            )
        return filters

    async def count(
        self,
        stage: Optional[str],
        source: Optional[str],
        counsellor_id: Optional[int],
        search: Optional[str],
        include_archived: bool,
    ) -> int:
        filters = self._build_filters(stage, source, counsellor_id, search, include_archived)
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
    ) -> list[CrmLead]:
        filters = self._build_filters(stage, source, counsellor_id, search, include_archived)
        stmt = (
            select(CrmLead)
            .where(*filters)
            .order_by(CrmLead.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get(self, lead_id: int) -> Optional[CrmLead]:
        stmt = (
            select(CrmLead)
            .where(CrmLead.lead_id == lead_id)
            .options(selectinload(CrmLead.activities))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> CrmLead:
        lead = CrmLead(**data)
        self.db.add(lead)
        await self.db.commit()
        await self.db.refresh(lead)
        # Re-fetch with activities loaded
        return await self.get(lead.lead_id)  # type: ignore[return-value]

    async def update(self, lead: CrmLead, data: dict[str, Any]) -> CrmLead:
        for field, value in data.items():
            setattr(lead, field, value)
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
        stmt = select(CrmLead.source, func.count().label("cnt")).group_by(CrmLead.source)
        return list((await self.db.execute(stmt)).all())

    async def stage_counts(self) -> list[Any]:
        stmt = select(CrmLead.stage, func.count().label("cnt")).group_by(CrmLead.stage)
        return list((await self.db.execute(stmt)).all())

    async def list_export(
        self, stage: Optional[str], source: Optional[str]
    ) -> list[CrmLead]:
        filters: list[Any] = []
        if stage:
            filters.append(CrmLead.stage == stage)
        if source:
            filters.append(CrmLead.source == source)
        stmt = select(CrmLead).where(*filters).order_by(CrmLead.created_at.desc())
        return list((await self.db.execute(stmt)).scalars().all())


class CrmActivityRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict[str, Any]) -> CrmActivity:
        activity = CrmActivity(**data)
        self.db.add(activity)
        await self.db.commit()
        await self.db.refresh(activity)
        return activity
