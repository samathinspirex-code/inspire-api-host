from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cms.models import Program
from app.modules.lms.models import LmsCourse


class CourseRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _filters(self, search: str | None, program_id: int | None, status: str | None) -> list[Any]:
        filters: list[Any] = []
        if search:
            pattern = f"%{search}%"
            filters.append(
                or_(LmsCourse.title.ilike(pattern), LmsCourse.code.ilike(pattern), Program.title.ilike(pattern))
            )
        if program_id is not None:
            filters.append(LmsCourse.program_id == program_id)
        if status:
            filters.append(LmsCourse.status == status)
        return filters

    async def list_courses(
        self, page: int, size: int, search: str | None, program_id: int | None, status: str | None
    ) -> tuple[list[tuple[LmsCourse, str, str]], int]:
        filters = self._filters(search, program_id, status)
        base = select(LmsCourse).join(Program, Program.program_id == LmsCourse.program_id).where(*filters)
        total = (await self.db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        stmt = (
            select(LmsCourse, Program.title, Program.code)
            .join(Program, Program.program_id == LmsCourse.program_id)
            .where(*filters)
            .order_by(LmsCourse.title)
            .offset((page - 1) * size)
            .limit(size)
        )
        rows = (await self.db.execute(stmt)).all()
        return [(row[0], row[1], row[2]) for row in rows], total

    async def get(self, course_id: int) -> LmsCourse | None:
        return await self.db.get(LmsCourse, course_id)

    async def get_with_program(self, course_id: int) -> tuple[LmsCourse, str, str] | None:
        stmt = (
            select(LmsCourse, Program.title, Program.code)
            .join(Program, Program.program_id == LmsCourse.program_id)
            .where(LmsCourse.course_id == course_id)
        )
        row = (await self.db.execute(stmt)).one_or_none()
        return (row[0], row[1], row[2]) if row else None

    async def get_by_code(self, code: str, exclude_course_id: int | None = None) -> LmsCourse | None:
        stmt = select(LmsCourse).where(func.lower(LmsCourse.code) == code.lower())
        if exclude_course_id is not None:
            stmt = stmt.where(LmsCourse.course_id != exclude_course_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> LmsCourse:
        course = LmsCourse(**data)
        self.db.add(course)
        await self.db.commit()
        await self.db.refresh(course)
        return course

    async def update(self, course: LmsCourse, data: dict[str, Any]) -> LmsCourse:
        for field, value in data.items():
            setattr(course, field, value)
        await self.db.commit()
        await self.db.refresh(course)
        return course

    async def delete(self, course: LmsCourse) -> None:
        await self.db.delete(course)
        await self.db.commit()
