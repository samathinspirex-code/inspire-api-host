from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.lms.models import (
    GoogleAccountConnection,
    GoogleCentralAccountConnection,
    GoogleCentralOAuthState,
    GoogleIntegrationSettings,
    GoogleOAuthState,
)


class IntegrationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_google_settings(self) -> GoogleIntegrationSettings | None:
        return await self.db.get(GoogleIntegrationSettings, 1)

    async def save_google_settings(self, data: dict, updated_by: int) -> GoogleIntegrationSettings:
        item = await self.get_google_settings()
        if item is None:
            item = GoogleIntegrationSettings(settings_id=1, updated_by=updated_by, **data)
            self.db.add(item)
        else:
            for field, value in data.items():
                setattr(item, field, value)
            item.updated_by = updated_by
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def create_oauth_state(
        self, state_hash: str, lecturer_user_id: int, expires_at: datetime
    ) -> None:
        await self.db.execute(
            delete(GoogleOAuthState).where(
                (GoogleOAuthState.lecturer_user_id == lecturer_user_id)
                | (GoogleOAuthState.expires_at < datetime.now(timezone.utc))
            )
        )
        self.db.add(
            GoogleOAuthState(
                state_hash=state_hash,
                lecturer_user_id=lecturer_user_id,
                expires_at=expires_at,
            )
        )
        await self.db.commit()

    async def consume_oauth_state(self, state_hash: str) -> GoogleOAuthState | None:
        stmt = select(GoogleOAuthState).where(GoogleOAuthState.state_hash == state_hash).with_for_update()
        item = (await self.db.execute(stmt)).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if item is None or item.used_at is not None or item.expires_at <= now:
            await self.db.rollback()
            return None
        item.used_at = now
        await self.db.commit()
        return item

    async def get_google_connection(self, lecturer_user_id: int) -> GoogleAccountConnection | None:
        return await self.db.get(GoogleAccountConnection, lecturer_user_id)

    async def save_google_connection(
        self,
        lecturer_user_id: int,
        google_subject: str,
        google_email: str,
        encrypted_refresh_token: str,
        granted_scopes: str,
    ) -> GoogleAccountConnection:
        item = await self.get_google_connection(lecturer_user_id)
        if item is None:
            item = GoogleAccountConnection(
                lecturer_user_id=lecturer_user_id,
                google_subject=google_subject,
                google_email=google_email,
                encrypted_refresh_token=encrypted_refresh_token,
                granted_scopes=granted_scopes,
            )
            self.db.add(item)
        else:
            item.google_subject = google_subject
            item.google_email = google_email
            item.encrypted_refresh_token = encrypted_refresh_token
            item.granted_scopes = granted_scopes
            item.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def delete_google_connection(self, lecturer_user_id: int) -> None:
        item = await self.get_google_connection(lecturer_user_id)
        if item is not None:
            await self.db.delete(item)
            await self.db.commit()

    async def create_central_oauth_state(
        self, state_hash: str, requested_by_user_id: int, expires_at: datetime
    ) -> None:
        await self.db.execute(
            delete(GoogleCentralOAuthState).where(
                (GoogleCentralOAuthState.requested_by_user_id == requested_by_user_id)
                | (GoogleCentralOAuthState.expires_at < datetime.now(timezone.utc))
            )
        )
        self.db.add(
            GoogleCentralOAuthState(
                state_hash=state_hash,
                requested_by_user_id=requested_by_user_id,
                expires_at=expires_at,
            )
        )
        await self.db.commit()

    async def consume_central_oauth_state(self, state_hash: str) -> GoogleCentralOAuthState | None:
        stmt = select(GoogleCentralOAuthState).where(
            GoogleCentralOAuthState.state_hash == state_hash
        ).with_for_update()
        item = (await self.db.execute(stmt)).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if item is None or item.used_at is not None or item.expires_at <= now:
            await self.db.rollback()
            return None
        item.used_at = now
        await self.db.commit()
        return item

    async def has_central_oauth_state(self, state_hash: str) -> bool:
        stmt = select(GoogleCentralOAuthState.state_hash).where(
            GoogleCentralOAuthState.state_hash == state_hash
        )
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def get_central_google_connection(self) -> GoogleCentralAccountConnection | None:
        return await self.db.get(GoogleCentralAccountConnection, 1)

    async def save_central_google_connection(
        self,
        google_subject: str,
        google_email: str,
        encrypted_refresh_token: str,
        granted_scopes: str,
        connected_by_user_id: int,
    ) -> GoogleCentralAccountConnection:
        item = await self.get_central_google_connection()
        if item is None:
            item = GoogleCentralAccountConnection(
                connection_id=1,
                google_subject=google_subject,
                google_email=google_email,
                encrypted_refresh_token=encrypted_refresh_token,
                granted_scopes=granted_scopes,
                connected_by_user_id=connected_by_user_id,
            )
            self.db.add(item)
        else:
            item.google_subject = google_subject
            item.google_email = google_email
            item.encrypted_refresh_token = encrypted_refresh_token
            item.granted_scopes = granted_scopes
            item.connected_by_user_id = connected_by_user_id
            item.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def delete_central_google_connection(self) -> None:
        item = await self.get_central_google_connection()
        if item is not None:
            await self.db.delete(item)
            await self.db.commit()
