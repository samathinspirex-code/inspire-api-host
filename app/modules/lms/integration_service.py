import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, ValidationError
from app.modules.lms.models import LecturerProfile
from app.modules.lms.repository import IntegrationRepository
from app.modules.lms.schemas import (
    GoogleConnectResponse,
    GoogleConnectionItem,
    GoogleIntegrationItem,
    GoogleIntegrationUpdate,
)

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
BASE_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/meetings.space.created",
]
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"


def _oauth_configured() -> bool:
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID.strip() and settings.GOOGLE_OAUTH_CLIENT_SECRET.strip())


def _encryption_configured() -> bool:
    try:
        Fernet(settings.GOOGLE_TOKEN_ENCRYPTION_KEY.encode())
        return True
    except (ValueError, TypeError):
        return False


def _fernet() -> Fernet:
    if not _encryption_configured():
        raise ValidationError("Google token encryption key is not configured correctly")
    return Fernet(settings.GOOGLE_TOKEN_ENCRYPTION_KEY.encode())


def _state_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _requested_scopes(item) -> list[str]:
    scopes = [*BASE_SCOPES]
    if item.calendar_sync_enabled:
        scopes.append(CALENDAR_SCOPE)
    return scopes


def _integration_ready(item) -> bool:
    return bool(
        item
        and item.enabled
        and item.workspace_domain
        and _oauth_configured()
        and _encryption_configured()
    )


def _to_item(item) -> GoogleIntegrationItem:
    oauth_configured = _oauth_configured()
    token_encryption_configured = _encryption_configured()
    enabled = item.enabled if item else False
    if not enabled:
        setup_status = "disabled"
    elif not oauth_configured:
        setup_status = "credentials_required"
    elif not token_encryption_configured:
        setup_status = "security_key_required"
    else:
        setup_status = "ready_for_account_connection"
    return GoogleIntegrationItem(
        enabled=enabled,
        workspace_domain=item.workspace_domain if item else None,
        embed_enabled=item.embed_enabled if item else True,
        calendar_sync_enabled=item.calendar_sync_enabled if item else True,
        attendance_sync_enabled=item.attendance_sync_enabled if item else True,
        attendance_threshold_percentage=(
            item.attendance_threshold_percentage if item else 50
        ),
        default_access_type=item.default_access_type if item else "restricted",
        oauth_configured=oauth_configured,
        token_encryption_configured=token_encryption_configured,
        oauth_redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
        setup_status=setup_status,
        updated_at=item.updated_at if item else None,
    )


async def get_google_integration(db: AsyncSession) -> GoogleIntegrationItem:
    return _to_item(await IntegrationRepository(db).get_google_settings())


async def update_google_integration(
    db: AsyncSession, payload: GoogleIntegrationUpdate, user_id: int
) -> GoogleIntegrationItem:
    data = payload.model_dump()
    domain = (payload.workspace_domain or "").strip().lower().removeprefix("@")
    data["workspace_domain"] = domain or None
    item = await IntegrationRepository(db).save_google_settings(data, user_id)
    return _to_item(item)


async def get_google_connection(db: AsyncSession, lecturer_user_id: int) -> GoogleConnectionItem:
    repository = IntegrationRepository(db)
    integration = await repository.get_google_settings()
    connection = await repository.get_google_connection(lecturer_user_id)
    has_lecturer_profile = await db.get(LecturerProfile, lecturer_user_id) is not None
    ready = _integration_ready(integration) and has_lecturer_profile
    if connection:
        message = "Your Google Workspace account is connected."
    elif not has_lecturer_profile:
        message = "Your Lecturer role is not linked to an LMS lecturer profile. Ask an Admin to complete the profile."
    elif ready:
        message = "Connect your college Google account to schedule online classes."
    else:
        message = "Google Workspace must be enabled and completed by a Super Admin first."
    return GoogleConnectionItem(
        integration_ready=ready,
        connected=connection is not None,
        google_email=connection.google_email if connection else None,
        granted_scopes=connection.granted_scopes.split() if connection else [],
        connected_at=connection.connected_at if connection else None,
        message=message,
    )


async def begin_google_connection(
    db: AsyncSession, lecturer_user_id: int, lecturer_email: str
) -> GoogleConnectResponse:
    repository = IntegrationRepository(db)
    if await db.get(LecturerProfile, lecturer_user_id) is None:
        raise ValidationError(
            "Your Lecturer role is not linked to an LMS lecturer profile. Ask an Admin to complete the profile."
        )
    integration = await repository.get_google_settings()
    if not _integration_ready(integration):
        raise ValidationError("Google Workspace integration is not ready. Ask a Super Admin to complete Settings.")

    raw_state = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.GOOGLE_OAUTH_STATE_EXPIRE_MINUTES
    )
    await repository.create_oauth_state(_state_hash(raw_state), lecturer_user_id, expires_at)
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(_requested_scopes(integration)),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": raw_state,
        "login_hint": lecturer_email,
    }
    if integration.workspace_domain not in {"gmail.com", "googlemail.com"}:
        params["hd"] = integration.workspace_domain
    return GoogleConnectResponse(authorization_url=f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}")


async def complete_google_connection(db: AsyncSession, code: str, state: str) -> str:
    repository = IntegrationRepository(db)
    oauth_state = await repository.consume_oauth_state(_state_hash(state))
    if oauth_state is None:
        raise ValidationError("This Google connection request is invalid or has expired")

    integration = await repository.get_google_settings()
    if not _integration_ready(integration):
        raise ValidationError("Google Workspace integration is no longer available")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            token_response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
            if token_response.is_error:
                raise ValidationError("Google could not complete the account connection. Please try again.")
            token_payload = token_response.json()
            access_token = token_payload.get("access_token")
            if not access_token:
                raise ValidationError("Google did not return an access token")

            user_response = await client.get(
                GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
            if user_response.is_error:
                raise ValidationError("The connected Google account could not be verified")
            google_user = user_response.json()
    except httpx.HTTPError as exc:
        raise ValidationError("Google is temporarily unavailable. Please try again.") from exc

    google_email = str(google_user.get("email", "")).strip().lower()
    google_subject = str(google_user.get("sub", "")).strip()
    if not google_email or not google_subject or not google_user.get("email_verified"):
        raise ValidationError("Google must provide a verified email address")
    expected_domain = integration.workspace_domain.strip().lower()
    actual_domain = google_email.rsplit("@", 1)[-1]
    if actual_domain != expected_domain:
        raise ValidationError(f"Connect an account from the {expected_domain} Google Workspace domain")

    existing = await repository.get_google_connection(oauth_state.lecturer_user_id)
    refresh_token = token_payload.get("refresh_token")
    if refresh_token:
        encrypted_refresh_token = _fernet().encrypt(refresh_token.encode()).decode()
    elif existing:
        encrypted_refresh_token = existing.encrypted_refresh_token
    else:
        raise ValidationError("Google did not provide offline access. Please connect again and approve access.")

    granted_scopes = str(token_payload.get("scope") or " ".join(_requested_scopes(integration)))
    try:
        await repository.save_google_connection(
            oauth_state.lecturer_user_id,
            google_subject,
            google_email,
            encrypted_refresh_token,
            granted_scopes,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("This Google account is already connected to another lecturer") from exc
    return google_email


async def cancel_google_connection(db: AsyncSession, state: str) -> None:
    oauth_state = await IntegrationRepository(db).consume_oauth_state(_state_hash(state))
    if oauth_state is None:
        raise ValidationError("This Google connection request is invalid or has expired")


async def disconnect_google_account(db: AsyncSession, lecturer_user_id: int) -> None:
    repository = IntegrationRepository(db)
    connection = await repository.get_google_connection(lecturer_user_id)
    if connection is None:
        return
    try:
        refresh_token = _fernet().decrypt(connection.encrypted_refresh_token.encode()).decode()
    except InvalidToken:
        refresh_token = ""
    if refresh_token:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(GOOGLE_REVOKE_URL, data={"token": refresh_token})
        except httpx.HTTPError:
            pass
    await repository.delete_google_connection(lecturer_user_id)


async def get_google_access_token(db: AsyncSession, lecturer_user_id: int) -> str:
    connection = await IntegrationRepository(db).get_google_connection(lecturer_user_id)
    if connection is None:
        raise ValidationError("Connect your Google account before scheduling an online meeting")
    try:
        refresh_token = _fernet().decrypt(connection.encrypted_refresh_token.encode()).decode()
    except InvalidToken as exc:
        raise ValidationError("Your Google connection can no longer be read. Disconnect and reconnect it.") from exc
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
    except httpx.HTTPError as exc:
        raise ValidationError("Google is temporarily unavailable. Please try again.") from exc
    if response.is_error or not response.json().get("access_token"):
        raise ValidationError("Your Google authorization has expired or was revoked. Reconnect the account.")
    return str(response.json()["access_token"])
