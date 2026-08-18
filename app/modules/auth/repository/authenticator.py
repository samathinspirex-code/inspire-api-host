from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.auth.models import (
    AuthenticatorCredential,
    AuthenticatorRecoveryCode,
    AuthenticatorSetupToken,
    User,
    UserAccessLevel,
)


class AuthenticatorRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def configured_user_ids(self, user_ids: list[int]) -> set[int]:
        if not user_ids:
            return set()
        stmt = select(AuthenticatorCredential.user_id).where(
            AuthenticatorCredential.user_id.in_(user_ids),
            AuthenticatorCredential.enabled.is_(True),
        )
        return set((await self.db.execute(stmt)).scalars().all())

    async def setup_statuses(
        self, user_ids: list[int]
    ) -> dict[int, tuple[str, datetime | None]]:
        if not user_ids:
            return {}
        configured = await self.configured_user_ids(user_ids)
        stmt = (
            select(AuthenticatorSetupToken)
            .where(AuthenticatorSetupToken.user_id.in_(user_ids))
            .order_by(
                AuthenticatorSetupToken.user_id,
                AuthenticatorSetupToken.created_at.desc(),
                AuthenticatorSetupToken.token_id.desc(),
            )
        )
        latest: dict[int, AuthenticatorSetupToken] = {}
        for token in (await self.db.execute(stmt)).scalars().all():
            latest.setdefault(token.user_id, token)

        now = datetime.now(timezone.utc)
        output: dict[int, tuple[str, datetime | None]] = {}
        for user_id in user_ids:
            if user_id in configured:
                output[user_id] = ("configured", None)
                continue
            token = latest.get(user_id)
            if token is None or token.used_at is not None:
                output[user_id] = ("not_invited", None)
            elif token.expires_at > now:
                output[user_id] = ("invitation_sent", token.expires_at)
            else:
                output[user_id] = ("invitation_expired", token.expires_at)
        return output

    async def create_setup_token(
        self,
        user_id: int,
        token_hash: str,
        expires_at: datetime,
        created_by: int | None,
    ) -> AuthenticatorSetupToken:
        await self.db.execute(
            delete(AuthenticatorSetupToken).where(
                AuthenticatorSetupToken.user_id == user_id,
                AuthenticatorSetupToken.used_at.is_(None),
            )
        )
        credential = await self.db.get(AuthenticatorCredential, user_id)
        if credential is not None:
            credential.enabled = False
            credential.last_used_step = None
            credential.failed_attempts = 0
            credential.locked_until = None
        await self.db.execute(
            delete(AuthenticatorRecoveryCode).where(AuthenticatorRecoveryCode.user_id == user_id)
        )
        item = AuthenticatorSetupToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by=created_by,
        )
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def get_valid_setup_token(self, token_hash: str, email: str):
        stmt = (
            select(AuthenticatorSetupToken, User)
            .join(User, User.user_id == AuthenticatorSetupToken.user_id)
            .options(
                selectinload(User.access_levels).selectinload(UserAccessLevel.access_level)
            )
            .where(
                AuthenticatorSetupToken.token_hash == token_hash,
                func.lower(User.email) == email,
                User.is_active.is_(True),
            )
            .with_for_update()
        )
        row = (await self.db.execute(stmt)).one_or_none()
        if row is None:
            return None
        token, user = row
        now = datetime.now(timezone.utc)
        if token.used_at is not None or token.expires_at <= now:
            await self.db.rollback()
            return None
        return token, user

    async def save_pending_secret(self, user_id: int, encrypted_secret: str) -> None:
        credential = await self.db.get(AuthenticatorCredential, user_id)
        if credential is None:
            credential = AuthenticatorCredential(
                user_id=user_id,
                encrypted_secret=encrypted_secret,
                enabled=False,
            )
            self.db.add(credential)
        else:
            credential.encrypted_secret = encrypted_secret
            credential.enabled = False
            credential.last_used_step = None
            credential.failed_attempts = 0
            credential.locked_until = None
        await self.db.commit()

    async def get_credential_for_update(self, user_id: int) -> AuthenticatorCredential | None:
        stmt = (
            select(AuthenticatorCredential)
            .where(AuthenticatorCredential.user_id == user_id)
            .with_for_update()
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def complete_setup(
        self,
        token: AuthenticatorSetupToken,
        credential: AuthenticatorCredential,
        used_step: int,
        recovery_code_hashes: list[str],
    ) -> None:
        now = datetime.now(timezone.utc)
        credential.enabled = True
        credential.last_used_step = used_step
        credential.failed_attempts = 0
        credential.locked_until = None
        credential.verified_at = now
        token.used_at = now
        await self.db.execute(
            delete(AuthenticatorRecoveryCode).where(
                AuthenticatorRecoveryCode.user_id == credential.user_id
            )
        )
        self.db.add_all([
            AuthenticatorRecoveryCode(user_id=credential.user_id, code_hash=value)
            for value in recovery_code_hashes
        ])
        await self.db.commit()

    async def record_failed_attempt(
        self, credential: AuthenticatorCredential, max_attempts: int, locked_until: datetime
    ) -> None:
        credential.failed_attempts += 1
        if credential.failed_attempts >= max_attempts:
            credential.failed_attempts = 0
            credential.locked_until = locked_until
        await self.db.commit()

    async def record_success(self, credential: AuthenticatorCredential, used_step: int) -> None:
        credential.last_used_step = used_step
        credential.failed_attempts = 0
        credential.locked_until = None
        await self.db.commit()

    async def get_recovery_code_for_update(self, user_id: int, code_hash: str):
        stmt = (
            select(AuthenticatorRecoveryCode)
            .where(
                AuthenticatorRecoveryCode.user_id == user_id,
                AuthenticatorRecoveryCode.code_hash == code_hash,
                AuthenticatorRecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def consume_recovery_code(self, item: AuthenticatorRecoveryCode) -> None:
        item.used_at = datetime.now(timezone.utc)
        await self.db.commit()
