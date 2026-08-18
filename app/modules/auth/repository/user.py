from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.auth.models import User, UserAccessLevel


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _with_access_levels(self, stmt):
        return stmt.options(selectinload(User.access_levels).selectinload(UserAccessLevel.access_level))

    async def get_by_email(self, email: str) -> Optional[User]:
        stmt = self._with_access_levels(select(User).where(func.lower(User.email) == email))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get(self, user_id: int) -> Optional[User]:
        stmt = self._with_access_levels(select(User).where(User.user_id == user_id))
        return (await self.db.execute(stmt)).scalar_one_or_none()
