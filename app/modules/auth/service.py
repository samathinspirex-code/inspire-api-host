import base64
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import quote, urlencode

import qrcode
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import APIError
from app.modules.auth.models import User
from app.modules.auth.invitation_email import send_authenticator_invitation
from app.modules.auth.rate_limit import check_ip_rate_limit
from app.modules.auth.repository import (
    AuthenticatorRepository,
    RefreshTokenRepository,
    SsoTicketRepository,
    UserRepository,
)
from app.modules.auth.schemas import (
    AuthenticatorSetupCompleteResponse,
    AuthenticatorInvitationResponse,
    AuthenticatorSetupStartResponse,
    AuthenticatorSetupTokenResponse,
    SsoTicketResponse,
    TokenResponse,
    UserOut,
)
from app.modules.auth.schemas.auth import AuthenticatorPortalLink
from app.modules.auth.security import (
    create_access_token,
    generate_recovery_codes,
    generate_refresh_token,
    generate_totp_secret,
    hash_value,
    normalize_recovery_code,
    verify_totp,
)


def _user_access_keys(user: User) -> list[str]:
    return [ual.access_level.access_key for ual in user.access_levels if ual.access_level.is_active]


async def _issue_tokens(db: AsyncSession, user: User) -> TokenResponse:
    access = _user_access_keys(user)
    access_token, expires_in = create_access_token(user.user_id, user.email, access)

    refresh_token_plain = generate_refresh_token()
    refresh_expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    await RefreshTokenRepository(db).create(user.user_id, hash_value(refresh_token_plain), refresh_expires_at)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token_plain,
        expires_in=expires_in,
        user=UserOut(user_id=user.user_id, email=user.email, full_name=user.full_name, access=access),
    )


def _authenticator_fernet() -> Fernet:
    try:
        return Fernet(settings.AUTHENTICATOR_ENCRYPTION_KEY.encode())
    except (ValueError, TypeError) as exc:
        raise APIError(
            503,
            "AUTHENTICATOR_NOT_CONFIGURED",
            "Authenticator security is not configured on the API server.",
        ) from exc


def _encrypt_authenticator_secret(secret: str) -> str:
    return _authenticator_fernet().encrypt(secret.encode()).decode()


def _decrypt_authenticator_secret(encrypted_secret: str) -> str:
    try:
        return _authenticator_fernet().decrypt(encrypted_secret.encode()).decode()
    except InvalidToken as exc:
        raise APIError(
            503,
            "AUTHENTICATOR_SECRET_UNREADABLE",
            "The stored Authenticator setup cannot be read. Ask an administrator to reset it.",
        ) from exc


def _provisioning_uri(email: str, secret: str) -> str:
    issuer = _authenticator_issuer()
    label = quote(f"{issuer}:{email}", safe="")
    query = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": 6,
            "period": 30,
        }
    )
    return f"otpauth://totp/{label}?{query}"


def _authenticator_issuer() -> str:
    return settings.AUTHENTICATOR_ISSUER.strip() or "Inspire College"


def _qr_data_url(value: str) -> str:
    image = qrcode.make(value)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}"


async def issue_authenticator_setup_token(
    db: AsyncSession, user_id: int, created_by: int | None
) -> AuthenticatorSetupTokenResponse:
    user = await UserRepository(db).get(user_id)
    if user is None or not user.is_active:
        raise APIError(404, "NOT_FOUND", "Active user not found.")
    plain_token = generate_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.AUTHENTICATOR_SETUP_EXPIRE_MINUTES
    )
    await AuthenticatorRepository(db).create_setup_token(
        user.user_id,
        hash_value(plain_token),
        expires_at,
        created_by,
    )
    await RefreshTokenRepository(db).revoke_all_for_user(user.user_id)
    return AuthenticatorSetupTokenResponse(
        user_id=user.user_id,
        email=user.email,
        setup_token=plain_token,
        expires_at=expires_at,
    )


def build_authenticator_setup_url(
    email: str, setup_token: str, setup_ui_url: str | None = None
) -> str:
    query = urlencode(
        {
            "setup": "authenticator",
            "email": email,
            "token": setup_token,
        }
    )
    base_url = setup_ui_url or settings.LMS_UI_URL
    return f"{base_url.rstrip('/')}?{query}"


async def issue_authenticator_setup_invitation(
    db: AsyncSession,
    user_id: int,
    created_by: int | None,
    setup_ui_url: str | None = None,
) -> AuthenticatorInvitationResponse:
    setup = await issue_authenticator_setup_token(db, user_id, created_by)
    user = await UserRepository(db).get(user_id)
    access = set(_user_access_keys(user))
    destinations = []
    if access & {"CMS", "USER_MANAGEMENT"}:
        destinations.append(("CMS", settings.CMS_UI_URL))
    if "LMS" in access:
        destinations.append(("LMS", settings.LMS_UI_URL))
    if not destinations:
        destinations.append(("Inspire", setup_ui_url or settings.LMS_UI_URL))
    # One account has one credential. Mint ONE token, then use it in each
    # authorized portal URL; issuing a second token would invalidate the first.
    portal_links = [AuthenticatorPortalLink(
        portal=portal,
        setup_url=build_authenticator_setup_url(setup.email, setup.setup_token, url),
        login_url=url.rstrip('/'),
    ) for portal, url in destinations]
    preferred_url = (setup_ui_url or settings.LMS_UI_URL).rstrip('/')
    primary = next((link for link in portal_links if link.login_url == preferred_url), portal_links[0])
    setup_url = primary.setup_url
    delivery = await send_authenticator_invitation(
        setup.email,
        user.full_name or setup.email,
        setup_url,
        setup.expires_at,
        f"authenticator-setup-{user_id}-{hash_value(setup.setup_token)[:20]}",
        portal_links=portal_links,
    )
    return AuthenticatorInvitationResponse(
        **setup.model_dump(),
        setup_url=setup_url,
        portal_links=portal_links,
        email_sent=delivery.sent,
        delivery_message=(
            "Authenticator setup invitation sent by email."
            if delivery.sent
            else f"{delivery.error or 'Email delivery failed'} Copy the setup link and send it manually."
        ),
    )


async def start_authenticator_setup(
    db: AsyncSession, email: str, setup_token: str
) -> AuthenticatorSetupStartResponse:
    normalized_email = email.strip().lower()
    repository = AuthenticatorRepository(db)
    row = await repository.get_valid_setup_token(hash_value(setup_token), normalized_email)
    if row is None:
        raise APIError(401, "SETUP_TOKEN_INVALID", "The Authenticator setup token is invalid or expired.")
    _token, user = row
    secret = generate_totp_secret()
    await repository.save_pending_secret(user.user_id, _encrypt_authenticator_secret(secret))
    uri = _provisioning_uri(user.email, secret)
    return AuthenticatorSetupStartResponse(
        issuer=_authenticator_issuer(),
        account_email=user.email,
        manual_key=secret,
        qr_code_data_url=_qr_data_url(uri),
    )


async def complete_authenticator_setup(
    db: AsyncSession,
    email: str,
    setup_token: str,
    code: str,
    client_ip: str,
) -> AuthenticatorSetupCompleteResponse:
    check_ip_rate_limit(client_ip)
    normalized_email = email.strip().lower()
    repository = AuthenticatorRepository(db)
    row = await repository.get_valid_setup_token(hash_value(setup_token), normalized_email)
    if row is None:
        raise APIError(401, "SETUP_TOKEN_INVALID", "The Authenticator setup token is invalid or expired.")
    token, token_user = row
    credential = await repository.get_credential_for_update(token_user.user_id)
    if credential is None:
        raise APIError(401, "SETUP_NOT_STARTED", "Start Authenticator setup and scan the QR code first.")
    now = datetime.now(timezone.utc)
    if credential.locked_until and credential.locked_until > now:
        raise APIError(429, "AUTHENTICATOR_LOCKED", "Too many incorrect codes. Try again later.")
    used_step = verify_totp(_decrypt_authenticator_secret(credential.encrypted_secret), code)
    if used_step is None:
        await repository.record_failed_attempt(
            credential,
            settings.AUTHENTICATOR_MAX_ATTEMPTS,
            now + timedelta(minutes=settings.AUTHENTICATOR_LOCK_MINUTES),
        )
        raise APIError(401, "AUTHENTICATOR_INVALID", "The Authenticator code is incorrect or expired.")

    recovery_codes = generate_recovery_codes()
    await repository.complete_setup(
        token,
        credential,
        used_step,
        [hash_value(normalize_recovery_code(value)) for value in recovery_codes],
    )
    tokens = await _issue_tokens(db, token_user)
    return AuthenticatorSetupCompleteResponse(**tokens.model_dump(), recovery_codes=recovery_codes)


async def verify_authenticator(
    db: AsyncSession, email: str, code: str, client_ip: str
) -> TokenResponse:
    check_ip_rate_limit(client_ip)
    normalized_email = email.strip().lower()
    user = await UserRepository(db).get_by_email(normalized_email)
    invalid = APIError(
        401,
        "AUTHENTICATOR_INVALID",
        "The email or Authenticator code is incorrect. Use setup if this is your first sign-in.",
    )
    if user is None or not user.is_active:
        raise invalid
    repository = AuthenticatorRepository(db)
    credential = await repository.get_credential_for_update(user.user_id)
    if credential is None or not credential.enabled:
        raise invalid
    now = datetime.now(timezone.utc)
    if credential.locked_until and credential.locked_until > now:
        raise APIError(429, "AUTHENTICATOR_LOCKED", "Too many incorrect codes. Try again later.")
    used_step = verify_totp(
        _decrypt_authenticator_secret(credential.encrypted_secret),
        code,
        last_used_step=credential.last_used_step,
    )
    if used_step is None:
        await repository.record_failed_attempt(
            credential,
            settings.AUTHENTICATOR_MAX_ATTEMPTS,
            now + timedelta(minutes=settings.AUTHENTICATOR_LOCK_MINUTES),
        )
        raise invalid
    await repository.record_success(credential, used_step)
    return await _issue_tokens(db, user)


async def verify_recovery_code(
    db: AsyncSession, email: str, recovery_code: str, client_ip: str
) -> TokenResponse:
    check_ip_rate_limit(client_ip)
    normalized_email = email.strip().lower()
    user = await UserRepository(db).get_by_email(normalized_email)
    invalid = APIError(401, "RECOVERY_CODE_INVALID", "The email or recovery code is incorrect.")
    if user is None or not user.is_active:
        raise invalid
    normalized_code = normalize_recovery_code(recovery_code)
    item = await AuthenticatorRepository(db).get_recovery_code_for_update(
        user.user_id, hash_value(normalized_code)
    )
    if item is None:
        raise invalid
    await AuthenticatorRepository(db).consume_recovery_code(item)
    return await _issue_tokens(db, user)


async def regenerate_recovery_codes(db: AsyncSession, user_id: int, code: str) -> list[str]:
    repository = AuthenticatorRepository(db)
    credential = await repository.get_credential_for_update(user_id)
    if credential is None or not credential.enabled:
        raise APIError(400, "AUTHENTICATOR_NOT_CONFIGURED", "Google Authenticator is not configured for this account.")
    now = datetime.now(timezone.utc)
    if credential.locked_until and credential.locked_until > now:
        raise APIError(429, "AUTHENTICATOR_LOCKED", "Too many incorrect codes. Try again later.")
    used_step = verify_totp(_decrypt_authenticator_secret(credential.encrypted_secret), code, last_used_step=credential.last_used_step)
    if used_step is None:
        await repository.record_failed_attempt(credential, settings.AUTHENTICATOR_MAX_ATTEMPTS,
            now + timedelta(minutes=settings.AUTHENTICATOR_LOCK_MINUTES))
        raise APIError(401, "AUTHENTICATOR_INVALID", "The Authenticator code is incorrect or expired.")
    credential.last_used_step = used_step
    credential.failed_attempts = 0
    credential.locked_until = None
    recovery_codes = generate_recovery_codes()
    await repository.replace_recovery_codes(user_id, [hash_value(normalize_recovery_code(value)) for value in recovery_codes])
    return recovery_codes


async def refresh_tokens(db: AsyncSession, refresh_token_plain: str) -> TokenResponse:
    refresh_repo = RefreshTokenRepository(db)
    token_hash = hash_value(refresh_token_plain)
    token_row = await refresh_repo.get_by_hash(token_hash)
    now = datetime.now(timezone.utc)

    if token_row is None or token_row.expires_at <= now:
        raise APIError(401, "REFRESH_INVALID", "Refresh token is invalid or expired.")

    if token_row.revoked_at is not None:
        await refresh_repo.revoke_all_for_user(token_row.user_id)
        raise APIError(401, "REFRESH_INVALID", "Refresh token is invalid or expired.")

    await refresh_repo.revoke(token_row)

    user = await UserRepository(db).get(token_row.user_id)
    if user is None or not user.is_active:
        raise APIError(401, "REFRESH_INVALID", "Refresh token is invalid or expired.")

    return await _issue_tokens(db, user)


async def logout(db: AsyncSession, refresh_token_plain: str) -> None:
    refresh_repo = RefreshTokenRepository(db)
    token_row = await refresh_repo.get_by_hash(hash_value(refresh_token_plain))
    if token_row is not None and token_row.revoked_at is None:
        await refresh_repo.revoke(token_row)


async def get_me(db: AsyncSession, user_id: int) -> UserOut:
    user = await UserRepository(db).get(user_id)
    if user is None or not user.is_active:
        raise APIError(401, "UNAUTHORIZED", "User not found or inactive.")

    return UserOut(user_id=user.user_id, email=user.email, full_name=user.full_name, access=_user_access_keys(user))


async def create_sso_ticket(db: AsyncSession, user_id: int) -> SsoTicketResponse:
    user = await UserRepository(db).get(user_id)
    if user is None or not user.is_active:
        raise APIError(401, "UNAUTHORIZED", "User not found or inactive.")

    access = set(_user_access_keys(user))
    lms_roles = {"SUPER_ADMIN", "ADMIN", "LECTURER", "STUDENT"}
    if "LMS" not in access or not access.intersection(lms_roles):
        raise APIError(403, "FORBIDDEN", "LMS access and an LMS role are required.")

    ticket_plain = generate_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.SSO_TICKET_EXPIRE_SECONDS)
    await SsoTicketRepository(db).create(user.user_id, hash_value(ticket_plain), expires_at)
    return SsoTicketResponse(ticket=ticket_plain, expires_in=settings.SSO_TICKET_EXPIRE_SECONDS)


async def exchange_sso_ticket(db: AsyncSession, ticket_plain: str) -> TokenResponse:
    ticket_repo = SsoTicketRepository(db)
    ticket = await ticket_repo.get_for_update(hash_value(ticket_plain))
    now = datetime.now(timezone.utc)

    if ticket is None or ticket.used_at is not None or ticket.expires_at <= now:
        raise APIError(401, "SSO_TICKET_INVALID", "The LMS sign-in link is invalid or expired.")

    await ticket_repo.mark_used(ticket)
    user = await UserRepository(db).get(ticket.user_id)
    if user is None or not user.is_active:
        raise APIError(401, "SSO_TICKET_INVALID", "The LMS sign-in link is invalid or expired.")

    access = set(_user_access_keys(user))
    lms_roles = {"SUPER_ADMIN", "ADMIN", "LECTURER", "STUDENT"}
    if "LMS" not in access or not access.intersection(lms_roles):
        raise APIError(403, "FORBIDDEN", "LMS access and an LMS role are required.")

    return await _issue_tokens(db, user)
