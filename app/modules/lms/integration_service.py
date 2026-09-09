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
    GoogleCentralConnectionItem,
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
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.settings",
]
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
DRIVE_MEET_SCOPE = "https://www.googleapis.com/auth/drive.meet.readonly"


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
    if item.attendance_sync_enabled:
        scopes.append(DRIVE_MEET_SCOPE)
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
    repository = IntegrationRepository(db)
    item = _to_item(await repository.get_google_settings())
    connection = await repository.get_central_google_connection()
    return item.model_copy(
        update={
            "central_account_connected": connection is not None,
            "central_google_email": connection.google_email if connection else None,
            "central_connected_at": connection.connected_at if connection else None,
            "central_granted_scopes": connection.granted_scopes.split() if connection else [],
        }
    )


async def update_google_integration(
    db: AsyncSession, payload: GoogleIntegrationUpdate, user_id: int
) -> GoogleIntegrationItem:
    data = payload.model_dump()
    domain = (payload.workspace_domain or "").strip().lower().removeprefix("@")
    data["workspace_domain"] = domain or None
    repository = IntegrationRepository(db)
    item = await repository.save_google_settings(data, user_id)
    response = _to_item(item)
    connection = await repository.get_central_google_connection()
    return response.model_copy(
        update={
            "central_account_connected": connection is not None,
            "central_google_email": connection.google_email if connection else None,
            "central_connected_at": connection.connected_at if connection else None,
            "central_granted_scopes": connection.granted_scopes.split() if connection else [],
        }
    )


async def get_central_google_connection(db: AsyncSession) -> GoogleCentralConnectionItem:
    repository = IntegrationRepository(db)
    integration = await repository.get_google_settings()
    connection = await repository.get_central_google_connection()
    ready = _integration_ready(integration)
    if connection:
        message = "This central Google account owns LMS meetings, calendar events, and attendance data."
    elif ready:
        message = "Connect the Google AI Plus account that will own all LMS meetings."
    else:
        message = "Complete Google Meet settings and server credentials before connecting the central account."
    return GoogleCentralConnectionItem(
        integration_ready=ready,
        connected=connection is not None,
        google_email=connection.google_email if connection else None,
        granted_scopes=connection.granted_scopes.split() if connection else [],
        connected_at=connection.connected_at if connection else None,
        message=message,
    )


async def begin_central_google_connection(
    db: AsyncSession, requested_by_user_id: int, login_hint: str
) -> GoogleConnectResponse:
    repository = IntegrationRepository(db)
    integration = await repository.get_google_settings()
    if not _integration_ready(integration):
        raise ValidationError("Google Meet integration is not ready. Save the Super Admin settings first.")
    raw_state = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.GOOGLE_OAUTH_STATE_EXPIRE_MINUTES)
    await repository.create_central_oauth_state(_state_hash(raw_state), requested_by_user_id, expires_at)
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(_requested_scopes(integration)),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": raw_state,
        "login_hint": login_hint,
    }
    if integration.workspace_domain not in {"gmail.com", "googlemail.com"}:
        params["hd"] = integration.workspace_domain
    return GoogleConnectResponse(authorization_url=f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}")


async def _exchange_google_code(code: str) -> tuple[dict, dict]:
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
            return token_payload, user_response.json()
    except httpx.HTTPError as exc:
        raise ValidationError("Google is temporarily unavailable. Please try again.") from exc


def _validate_google_identity(google_user: dict, integration) -> tuple[str, str]:
    google_email = str(google_user.get("email", "")).strip().lower()
    google_subject = str(google_user.get("sub", "")).strip()
    if not google_email or not google_subject or not google_user.get("email_verified"):
        raise ValidationError("Google must provide a verified email address")
    expected_domain = integration.workspace_domain.strip().lower()
    actual_domain = google_email.rsplit("@", 1)[-1]
    if actual_domain != expected_domain:
        raise ValidationError(f"Connect an account from the {expected_domain} Google Workspace domain")
    return google_email, google_subject


async def complete_central_google_connection(db: AsyncSession, code: str, state: str) -> str:
    repository = IntegrationRepository(db)
    oauth_state = await repository.consume_central_oauth_state(_state_hash(state))
    if oauth_state is None:
        raise ValidationError("This central Google connection request is invalid or has expired")
    integration = await repository.get_google_settings()
    if not _integration_ready(integration):
        raise ValidationError("Google Meet integration is no longer available")
    token_payload, google_user = await _exchange_google_code(code)
    google_email, google_subject = _validate_google_identity(google_user, integration)
    existing = await repository.get_central_google_connection()
    refresh_token = token_payload.get("refresh_token")
    if refresh_token:
        encrypted_refresh_token = _fernet().encrypt(refresh_token.encode()).decode()
    elif existing:
        encrypted_refresh_token = existing.encrypted_refresh_token
    else:
        raise ValidationError("Google did not provide offline access. Connect again and approve all requested access.")
    await repository.save_central_google_connection(
        google_subject,
        google_email,
        encrypted_refresh_token,
        str(token_payload.get("scope") or " ".join(_requested_scopes(integration))),
        oauth_state.requested_by_user_id,
    )
    return google_email


async def cancel_central_google_connection(db: AsyncSession, state: str) -> None:
    oauth_state = await IntegrationRepository(db).consume_central_oauth_state(_state_hash(state))
    if oauth_state is None:
        raise ValidationError("This central Google connection request is invalid or has expired")


async def is_central_google_connection_state(db: AsyncSession, state: str) -> bool:
    return await IntegrationRepository(db).has_central_oauth_state(_state_hash(state))


async def disconnect_central_google_account(db: AsyncSession) -> None:
    repository = IntegrationRepository(db)
    connection = await repository.get_central_google_connection()
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
    await repository.delete_central_google_connection()


async def get_google_connection(db: AsyncSession, lecturer_user_id: int) -> GoogleConnectionItem:
    repository = IntegrationRepository(db)
    integration = await repository.get_google_settings()
    has_lecturer_profile = await db.get(LecturerProfile, lecturer_user_id) is not None
    connection = await repository.get_central_google_connection()
    ready = _integration_ready(integration) and has_lecturer_profile and connection is not None
    if connection:
        message = "Your classes use the centrally managed Google meeting account. A Super Admin adds the lecturer as a Google Meet co-host from the Calendar event."
    elif not has_lecturer_profile:
        message = "Your Lecturer role is not linked to an LMS lecturer profile. Ask an Admin to complete the profile."
    elif _integration_ready(integration):
        message = "A Super Admin must connect the central Google meeting account before classes can be scheduled."
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

    token_payload, google_user = await _exchange_google_code(code)
    google_email, google_subject = _validate_google_identity(google_user, integration)

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


async def get_central_google_access_token(db: AsyncSession) -> str:
    connection = await IntegrationRepository(db).get_central_google_connection()
    if connection is None:
        raise ValidationError("A Super Admin must connect the central Google meeting account before classes can be scheduled")
    try:
        refresh_token = _fernet().decrypt(connection.encrypted_refresh_token.encode()).decode()
    except InvalidToken as exc:
        raise ValidationError("The central Google account can no longer be read. Reconnect it in Super Admin Settings.") from exc
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
        raise ValidationError("The central Google authorization has expired or was revoked. Reconnect it in Super Admin Settings.")
    return str(response.json()["access_token"])
