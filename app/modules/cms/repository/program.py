from typing import Any, Optional

from sqlalchemy import Row, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cms.models import Program


class ProgramRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _filters(self, search: Optional[str]) -> list[Any]:
        filters: list[Any] = []
        if search:
            pattern = f"%{search}%"
            filters.append(
                or_(
                    Program.title.ilike(pattern),
                    Program.school.ilike(pattern),
                    Program.awarding_body.ilike(pattern),
                    Program.level.ilike(pattern),
                )
            )
        return filters

    async def count(self, search: Optional[str]) -> int:
        filters = self._filters(search)
        stmt = select(func.count()).select_from(select(Program).where(*filters).subquery())
        return (await self.db.execute(stmt)).scalar_one()

    async def list_programs(
        self, search: Optional[str], page: int, size: int
    ) -> list[Program]:
        filters = self._filters(search)
        stmt = (
            select(Program)
            .where(*filters)
            .order_by(Program.program_id)
            .offset((page - 1) * size)
            .limit(size)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def create(self, data: dict[str, Any]) -> Program:
        program = Program(**data)
        self.db.add(program)
        await self.db.commit()
        await self.db.refresh(program)
        return program

    async def get(self, program_id: int) -> Optional[Program]:
        return await self.db.get(Program, program_id)

    async def update(self, program: Program, data: dict[str, Any]) -> Program:
        for field, value in data.items():
            setattr(program, field, value)
        await self.db.commit()
        await self.db.refresh(program)
        return program

    async def delete(self, program: Program) -> None:
        await self.db.delete(program)
        await self.db.commit()

    async def list_all_programs(self) -> list[Program]:
        stmt = select(Program).order_by(Program.popularity.desc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_by_slug(self, slug: str) -> Optional[Program]:
        stmt = select(Program).where(Program.slug == slug)
        return (await self.db.execute(stmt)).scalar_one_or_none()

