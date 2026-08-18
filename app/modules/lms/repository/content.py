from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.lms.models import LmsCourseDiscussion, LmsLearningItem, LmsModuleAccess


class ContentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_items(self, module_id: int) -> list[LmsLearningItem]:
        stmt = select(LmsLearningItem).where(LmsLearningItem.module_id == module_id).order_by(LmsLearningItem.position)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_item(self, learning_item_id: int) -> LmsLearningItem | None:
        return await self.db.get(LmsLearningItem, learning_item_id)

    async def next_position(self, module_id: int) -> int:
        stmt = select(func.coalesce(func.max(LmsLearningItem.position), 0) + 1).where(
            LmsLearningItem.module_id == module_id
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def create_item(self, data: dict[str, Any]) -> LmsLearningItem:
        item = LmsLearningItem(**data)
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def update_item(self, item: LmsLearningItem, data: dict[str, Any]) -> LmsLearningItem:
        for field, value in data.items():
            setattr(item, field, value)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def delete_item(self, item: LmsLearningItem) -> None:
        module_id, deleted_position = item.module_id, item.position
        await self.db.delete(item)
        await self.db.flush()
        await self.db.execute(
            update(LmsLearningItem)
            .where(LmsLearningItem.module_id == module_id, LmsLearningItem.position > deleted_position)
            .values(position=LmsLearningItem.position - 1)
        )
        await self.db.commit()

    async def reorder_items(self, items: list[LmsLearningItem], item_ids: list[int]) -> list[LmsLearningItem]:
        by_id = {item.learning_item_id: item for item in items}
        offset = len(items) + 1000
        for item in items:
            item.position += offset
        await self.db.flush()
        for position, item_id in enumerate(item_ids, start=1):
            by_id[item_id].position = position
        await self.db.commit()
        return await self.list_items(items[0].module_id) if items else []

    async def list_access(self, module_id: int) -> list[LmsModuleAccess]:
        stmt = select(LmsModuleAccess).where(LmsModuleAccess.module_id == module_id).order_by(LmsModuleAccess.scope_type)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_access(self, module_id: int, scope_type: str, scope_id: int) -> LmsModuleAccess | None:
        stmt = select(LmsModuleAccess).where(
            LmsModuleAccess.module_id == module_id,
            LmsModuleAccess.scope_type == scope_type,
            LmsModuleAccess.scope_id == scope_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def upsert_access(self, module_id: int, data: dict[str, Any]) -> LmsModuleAccess:
        item = await self.get_access(module_id, data["scope_type"], data["scope_id"])
        if item is None:
            item = LmsModuleAccess(module_id=module_id, **data)
            self.db.add(item)
        else:
            item.is_unlocked = data["is_unlocked"]
            item.available_from = data.get("available_from")
            item.created_by = data.get("created_by")
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def list_discussions(self, course_id: int, limit: int = 200):
        stmt = (
            select(LmsCourseDiscussion, User.full_name, User.email)
            .join(User, User.user_id == LmsCourseDiscussion.author_user_id)
            .where(LmsCourseDiscussion.course_id == course_id)
            .order_by(LmsCourseDiscussion.created_at.asc(), LmsCourseDiscussion.discussion_id.asc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).all())

    async def create_discussion(self, course_id: int, user_id: int, message: str) -> LmsCourseDiscussion:
        item = LmsCourseDiscussion(course_id=course_id, author_user_id=user_id, message=message)
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item
