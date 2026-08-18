from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.lms.models import LmsModule


class ModuleRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_course(self, course_id: int) -> list[LmsModule]:
        stmt = select(LmsModule).where(LmsModule.course_id == course_id).order_by(LmsModule.position)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get(self, module_id: int) -> LmsModule | None:
        return await self.db.get(LmsModule, module_id)

    async def next_position(self, course_id: int) -> int:
        stmt = select(func.coalesce(func.max(LmsModule.position), 0) + 1).where(LmsModule.course_id == course_id)
        return (await self.db.execute(stmt)).scalar_one()

    async def create(self, data: dict[str, Any]) -> LmsModule:
        module = LmsModule(**data)
        self.db.add(module)
        await self.db.commit()
        await self.db.refresh(module)
        return module

    async def update(self, module: LmsModule, data: dict[str, Any]) -> LmsModule:
        for field, value in data.items():
            setattr(module, field, value)
        await self.db.commit()
        await self.db.refresh(module)
        return module

    async def delete_and_renumber(self, module: LmsModule) -> None:
        course_id = module.course_id
        deleted_position = module.position
        await self.db.delete(module)
        await self.db.flush()
        stmt = (
            update(LmsModule)
            .where(LmsModule.course_id == course_id, LmsModule.position > deleted_position)
            .values(position=LmsModule.position - 1)
        )
        await self.db.execute(stmt)
        await self.db.commit()

    async def reorder(self, modules: list[LmsModule], module_ids: list[int]) -> list[LmsModule]:
        by_id = {module.module_id: module for module in modules}
        offset = len(modules) + 1000
        for module in modules:
            module.position += offset
        await self.db.flush()
        for position, module_id in enumerate(module_ids, start=1):
            by_id[module_id].position = position
        await self.db.commit()
        return await self.list_by_course(modules[0].course_id) if modules else []
