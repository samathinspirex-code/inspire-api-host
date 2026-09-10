from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cms.models.news_event import NewsEvent


class NewsEventRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _filters(
        self,
        search: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        category: Optional[str] = None,
    ) -> list[Any]:
        filters: list[Any] = []
        if search:
            pattern = f"%{search}%"
            filters.append(or_(NewsEvent.title.ilike(pattern), NewsEvent.excerpt.ilike(pattern), NewsEvent.author.ilike(pattern)))
        if status:
            filters.append(NewsEvent.status == status)
        if kind:
            filters.append(NewsEvent.kind == kind)
        if category:
            filters.append(NewsEvent.category == category)
        return filters

    async def count(self, **filters: Optional[str]) -> int:
        where = self._filters(**filters)
        stmt = select(func.count()).select_from(select(NewsEvent).where(*where).subquery())
        return (await self.db.execute(stmt)).scalar_one()

    async def list(self, page: int, size: int, **filters: Optional[str]) -> list[NewsEvent]:
        where = self._filters(**filters)
        stmt = (
            select(NewsEvent)
            .where(*where)
            .order_by(NewsEvent.updated_at.desc(), NewsEvent.news_event_id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_published(
        self, limit: int, kind: Optional[str] = None, category: Optional[str] = None
    ) -> list[NewsEvent]:
        where = self._filters(status="Published", kind=kind, category=category)
        stmt = (
            select(NewsEvent)
            .where(*where)
            .order_by(NewsEvent.published_on.desc().nullslast(), NewsEvent.news_event_id.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def create(self, data: dict[str, Any]) -> NewsEvent:
        item = NewsEvent(**data)
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def get(self, news_event_id: int) -> Optional[NewsEvent]:
        return await self.db.get(NewsEvent, news_event_id)

    async def get_by_slug(self, slug: str) -> Optional[NewsEvent]:
        stmt = select(NewsEvent).where(NewsEvent.slug == slug)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_published_by_slug(self, slug: str) -> Optional[NewsEvent]:
        stmt = select(NewsEvent).where(NewsEvent.slug == slug, NewsEvent.status == "Published")
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def update(self, item: NewsEvent, data: dict[str, Any]) -> NewsEvent:
        for field, value in data.items():
            setattr(item, field, value)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def delete(self, item: NewsEvent) -> None:
        await self.db.delete(item)
        await self.db.commit()
