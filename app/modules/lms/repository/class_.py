from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cms.models import Program
from app.modules.lms.models import LmsClass, LmsCourse


class ClassRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _filters(self, search: str | None, course_id: int | None, status: str | None) -> list[Any]:
        filters: list[Any] = []
        if search:
            pattern = f"%{search}%"
            filters.append(
                or_(
                    LmsClass.name.ilike(pattern),
                    LmsClass.code.ilike(pattern),
                    LmsCourse.title.ilike(pattern),
                )
            )
        if course_id is not None:
            filters.append(LmsClass.course_id == course_id)
        if status:
            filters.append(LmsClass.status == status)
        return filters

    async def list_classes(
        self, page: int, size: int, search: str | None, course_id: int | None, status: str | None
    ) -> tuple[list[tuple[LmsClass, str, str, str]], int]:
        filters = self._filters(search, course_id, status)
        base = (
            select(LmsClass)
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
            .join(Program, Program.program_id == LmsCourse.program_id)
            .where(*filters)
        )
        total = (await self.db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        stmt = (
            select(LmsClass, LmsCourse.code, LmsCourse.title, Program.title)
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
            .join(Program, Program.program_id == LmsCourse.program_id)
            .where(*filters)
            .order_by(LmsClass.start_date.desc(), LmsClass.name)
            .offset((page - 1) * size)
            .limit(size)
        )
        rows = (await self.db.execute(stmt)).all()
        return [(row[0], row[1], row[2], row[3]) for row in rows], total

    async def get(self, class_id: int) -> LmsClass | None:
        return await self.db.get(LmsClass, class_id)

    async def get_for_update(self, class_id: int) -> LmsClass | None:
        stmt = select(LmsClass).where(LmsClass.class_id == class_id).with_for_update()
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_code(self, code: str, exclude_class_id: int | None = None) -> LmsClass | None:
        stmt = select(LmsClass).where(func.lower(LmsClass.code) == code.lower())
        if exclude_class_id is not None:
            stmt = stmt.where(LmsClass.class_id != exclude_class_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> LmsClass:
        class_ = LmsClass(**data)
        self.db.add(class_)
        await self.db.commit()
        await self.db.refresh(class_)
        return class_

    async def update(self, class_: LmsClass, data: dict[str, Any]) -> LmsClass:
        for field, value in data.items():
            setattr(class_, field, value)
        await self.db.commit()
        await self.db.refresh(class_)
        return class_

    async def delete(self, class_: LmsClass) -> None:
        await self.db.delete(class_)
        await self.db.commit()
