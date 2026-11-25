from typing import Any, List, Optional

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cms.models.news_event import NewsEvent


# Website order: CMS position first (0 = not arranged, so it leads), then newest first.
WEBSITE_ORDER = (
    NewsEvent.position.asc(),
    NewsEvent.published_on.desc().nullslast(),
    NewsEvent.news_event_id.desc(),
)


class NewsEventRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _filters(
        self,
        search: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[Any]:
        filters: List[Any] = []
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

    async def list(self, page: int, size: int, **filters: Optional[str]) -> List[NewsEvent]:
        where = self._filters(**filters)
        stmt = (
            select(NewsEvent)
            .where(*where)
            .order_by(*WEBSITE_ORDER)
            .offset((page - 1) * size)
            .limit(size)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_published(
        self, limit: int, kind: Optional[str] = None, category: Optional[str] = None
    ) -> List[NewsEvent]:
        where = self._filters(status="Published", kind=kind, category=category)
        stmt = (
            select(NewsEvent)
            .where(*where)
            .order_by(*WEBSITE_ORDER)
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

    async def ids_in_website_order(self) -> list[int]:
        stmt = select(NewsEvent.news_event_id).order_by(*WEBSITE_ORDER)
        return list((await self.db.execute(stmt)).scalars().all())

    async def reorder(self, ordered_ids: list[int]) -> None:
        """Give the listed posts positions 1..n in the order given."""
        positions = {news_event_id: index for index, news_event_id in enumerate(ordered_ids, start=1)}
        await self.db.execute(
            update(NewsEvent)
            .where(NewsEvent.news_event_id.in_(ordered_ids))
            .values(position=case(positions, value=NewsEvent.news_event_id))
            .execution_options(synchronize_session=False)
        )
        await self.db.commit()

    async def delete(self, item: NewsEvent) -> None:
        await self.db.delete(item)
        await self.db.commit()
