from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import AuthenticatorSetupToken, PasswordCredential


class PasswordRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def configured_user_ids(self, user_ids: list[int]) -> set[int]:
        if not user_ids:
            return set()
        result = await self.db.execute(
            select(PasswordCredential.user_id).where(PasswordCredential.user_id.in_(user_ids))
        )
        return set(result.scalars().all())

    async def get_for_update(self, user_id: int) -> PasswordCredential | None:
        return (await self.db.execute(
            select(PasswordCredential)
            .where(PasswordCredential.user_id == user_id)
            .with_for_update()
        )).scalar_one_or_none()

    async def complete_setup(
        self, token: AuthenticatorSetupToken, user_id: int, password_hash: str
    ) -> None:
        now = datetime.now(timezone.utc)
        credential = await self.get_for_update(user_id)
        if credential is None:
            credential = PasswordCredential(
                user_id=user_id, password_hash=password_hash, verified_at=now
            )
            self.db.add(credential)
        else:
            credential.password_hash = password_hash
            credential.failed_attempts = 0
            credential.locked_until = None
            credential.verified_at = now
        token.used_at = now
        await self.db.execute(
            update(AuthenticatorSetupToken)
            .where(
                AuthenticatorSetupToken.user_id == user_id,
                AuthenticatorSetupToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
        await self.db.commit()

    async def record_failed_attempt(
        self, credential: PasswordCredential, max_attempts: int, locked_until: datetime
    ) -> None:
        credential.failed_attempts += 1
        if credential.failed_attempts >= max_attempts:
            credential.failed_attempts = 0
            credential.locked_until = locked_until
        await self.db.commit()

    async def record_success(self, credential: PasswordCredential) -> None:
        credential.failed_attempts = 0
        credential.locked_until = None
        await self.db.commit()
