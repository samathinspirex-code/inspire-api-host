from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.auth.models import (
    AccessLevel,
    AuthenticatorCredential,
    AuthenticatorRecoveryCode,
    AuthenticatorSetupToken,
    PasswordCredential,
    RefreshToken,
    SsoTicket,
    User,
    UserAccessLevel,
)


class UserManagementRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _with_access_levels(self, stmt):
        return stmt.options(selectinload(User.access_levels).selectinload(UserAccessLevel.access_level))

    async def list_all(self) -> list[User]:
        stmt = self._with_access_levels(select(User).where(User.is_active.is_(True)).order_by(User.user_id))
        return list((await self.db.execute(stmt)).scalars().all())

    async def get(self, user_id: int) -> Optional[User]:
        stmt = self._with_access_levels(select(User).where(User.user_id == user_id, User.is_active.is_(True)))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_email(self, email: str, exclude_user_id: Optional[int] = None) -> Optional[User]:
        stmt = select(User).where(func.lower(User.email) == email)
        if exclude_user_id is not None:
            stmt = stmt.where(User.user_id != exclude_user_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_access_levels_by_keys(self, keys: list[str]) -> list[AccessLevel]:
        if not keys:
            return []
        stmt = select(AccessLevel).where(AccessLevel.access_key.in_(keys))
        return list((await self.db.execute(stmt)).scalars().all())

    async def create(self, name: str, email: str, access_levels: list[AccessLevel]) -> User:
        user = User(full_name=name, email=email)
        user.access_levels = [UserAccessLevel(access_level=al) for al in access_levels]
        self.db.add(user)
        await self.db.commit()
        return user

    async def update(self, user: User, name: str, email: str, access_levels: list[AccessLevel]) -> User:
        user.full_name = name
        user.email = email
        user.access_levels = [UserAccessLevel(access_level=al) for al in access_levels]
        await self.db.commit()
        return user

    async def delete(self, user: User) -> None:
        # Preserve academic and CRM history that references the user. Revoking
        # the account also lets the original email be used for a new account.
        user.is_active = False
        user.email = f"deleted+{user.user_id}@deleted.inspire.college"
        user.full_name = f"Deleted user {user.user_id}"
        user.access_levels.clear()
        for model in (
            RefreshToken,
            SsoTicket,
            AuthenticatorSetupToken,
            AuthenticatorRecoveryCode,
            AuthenticatorCredential,
            PasswordCredential,
        ):
            await self.db.execute(delete(model).where(model.user_id == user.user_id))
        await self.db.commit()
