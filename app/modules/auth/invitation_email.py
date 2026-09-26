import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.core.config import settings
from app.modules.auth.schemas.auth import AuthenticatorPortalLink

logger = logging.getLogger(__name__)

MAILJET_SEND_URL = "https://api.mailjet.com/v3.1/send"
MAILJET_SEND_ATTEMPTS = 3
MAILJET_RETRY_DELAYS_SECONDS = (0.5, 1.5)


@dataclass(frozen=True)
class EmailDeliveryResult:
    sent: bool
    provider_message_id: str | None = None
    error: str | None = None


def _portal_links(setup_url: str, portal_links: list[AuthenticatorPortalLink] | None):
    return portal_links or [AuthenticatorPortalLink(portal="Inspire", setup_url=setup_url)]


def build_invitation_html(full_name: str, setup_url: str, expires_at: datetime, portal_links: list[AuthenticatorPortalLink] | None = None, setup_method: str = "authenticator") -> str:
    safe_name = html.escape(full_name or "Inspire user")
    safe_expiry = html.escape(expires_at.strftime("%d %B %Y at %H:%M UTC"))
    links = _portal_links(setup_url, portal_links)
    password_setup = setup_method in {"password", "password_reset"}
    password_reset = setup_method == "password_reset"
    setup_label = "Reset password" if password_reset else "Set up password" if password_setup else "Set up Authenticator"
    setup_buttons = "".join(
        f'<div style="margin:20px 0">'
        f'<p><a href="{html.escape(link.setup_url, quote=True)}" style="display:inline-block;padding:12px 24px;background-color:#3f007c;color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;border-radius:6px">{setup_label} — {html.escape(link.portal)}</a></p>'
        f'<p style="font-size:12px;color:#5f6368;margin-top:6px">Direct link: <a href="{html.escape(link.setup_url, quote=True)}" style="color:#3f007c;word-break:break-all">{html.escape(link.setup_url)}</a></p>'
        f'</div>'
        for link in links
    )
    logins = " · ".join(
        f'<a href="{html.escape(link.login_url, quote=True)}">Open {html.escape(link.portal)}</a>'
        for link in links if link.login_url
    )
    guidance = ("Choose either portal link to set up Authenticator once. The same Authenticator works for both CMS and LMS. "
                "Completing setup uses the shared invitation, so both setup links become invalid.") if len(links) > 1 else "Use the secure link below to connect Google Authenticator."
    if password_setup:
        guidance = "Use the secure link below to create your password. The link works once and must not be shared."
    account_setup = "password reset" if password_reset else "password setup" if password_setup else "Authenticator setup"
    login_method = "email address and password" if password_setup else "Authenticator code"
    return f"""
    <div style="font-family:Arial,sans-serif;line-height:1.55;color:#202124;max-width:560px">
      <p>Hello {safe_name},</p>
      <p>Your Inspire College account is ready for {account_setup}.</p>
      <p>{guidance}</p>
      {setup_buttons}
      <p>This single-use invitation expires on {safe_expiry}.</p>
      {f'<p>After setup, sign in using your {login_method}: {logins}</p>' if logins else ''}
      <p>If you did not expect this account, contact your administrator. Do not forward this message or share its setup links.</p>
      <p>Inspire College</p>
    </div>
    """.strip()


def build_invitation_text(full_name: str, setup_url: str, expires_at: datetime, portal_links: list[AuthenticatorPortalLink] | None = None, setup_method: str = "authenticator") -> str:
    links = _portal_links(setup_url, portal_links)
    password_setup = setup_method in {"password", "password_reset"}
    password_reset = setup_method == "password_reset"
    setup_label = "Reset password" if password_reset else "Set up password" if password_setup else "Set up Authenticator"
    setup_lines = "\n".join(f"{link.portal} — {setup_label}: {link.setup_url}" for link in links)
    logins = "\n".join(f"Open {link.portal}: {link.login_url}" for link in links if link.login_url)
    login_section = f"After setup, sign in using your Authenticator code:\n{logins}\n\n" if logins else ""
    guidance = ("Choose either portal link to set up Authenticator once. The same Authenticator works for both CMS and LMS. "
                "Completing setup uses the shared invitation, so both setup links become invalid.") if len(links) > 1 else "Use the secure link below to connect Google Authenticator."
    if password_setup:
        guidance = "Use the secure link below to create your password. The link works once and must not be shared."
        login_section = f"After setup, sign in using your email address and password:\n{logins}\n\n" if logins else ""
    account_setup = "password reset" if password_reset else "password setup" if password_setup else "Authenticator setup"
    return (
        f"Hello {full_name or 'Inspire user'},\n\n"
        f"Your Inspire College account is ready for {account_setup}.\n\n"
        f"{guidance}\n\n{setup_lines}\n\n"
        f"This single-use invitation expires on {expires_at.strftime('%d %B %Y at %H:%M UTC')}.\n\n"
        f"{login_section}"
        "If you did not expect this account, contact your administrator. "
        "Do not forward this message or share its setup links.\n\n"
        "Inspire College"
    )


def build_mailjet_payload(
    to_email: str,
    full_name: str,
    setup_url: str,
    expires_at: datetime,
    custom_id: str,
    portal_links: list[AuthenticatorPortalLink] | None = None,
    setup_method: str = "authenticator",
) -> dict:
    return {
        "Messages": [
            {
                "From": {
                    "Email": settings.MAILJET_FROM_EMAIL,
                    "Name": settings.MAILJET_FROM_NAME,
                },
                "To": [{"Email": to_email, "Name": full_name}],
                "Subject": ("Reset your Inspire College password" if setup_method == "password_reset" else "Set up your Inspire College password") if setup_method in {"password", "password_reset"} else settings.AUTHENTICATOR_INVITATION_SUBJECT,
                "TextPart": build_invitation_text(full_name, setup_url, expires_at, portal_links, setup_method),
                "HTMLPart": build_invitation_html(full_name, setup_url, expires_at, portal_links, setup_method),
                "CustomID": custom_id,
                "TrackOpens": "disabled",
                "TrackClicks": "disabled",
            }
        ]
    }


async def send_authenticator_invitation(
    to_email: str,
    full_name: str,
    setup_url: str,
    expires_at: datetime,
    idempotency_key: str,
    portal_links: list[AuthenticatorPortalLink] | None = None,
    setup_method: str = "authenticator",
) -> EmailDeliveryResult:
    if not settings.MAILJET_API_KEY.strip() or not settings.MAILJET_SECRET_KEY.strip():
        return EmailDeliveryResult(False, error="The Mailjet API credentials are not configured.")
    if not settings.MAILJET_FROM_EMAIL.strip():
        return EmailDeliveryResult(False, error="The Mailjet sender email is not configured.")

    payload = build_mailjet_payload(
        to_email, full_name, setup_url, expires_at, idempotency_key, portal_links, setup_method
    )
    try:
        response: httpx.Response | None = None
        last_transport_error: httpx.HTTPError | None = None
        timeout = httpx.Timeout(30.0, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(MAILJET_SEND_ATTEMPTS):
                try:
                    response = await client.post(
                        MAILJET_SEND_URL,
                        json=payload,
                        auth=httpx.BasicAuth(
                            settings.MAILJET_API_KEY, settings.MAILJET_SECRET_KEY
                        ),
                    )
                except httpx.HTTPError as exc:
                    last_transport_error = exc
                    logger.warning(
                        "Mailjet invitation attempt %s/%s failed for user email domain %s: %s",
                        attempt + 1,
                        MAILJET_SEND_ATTEMPTS,
                        to_email.rsplit("@", 1)[-1],
                        type(exc).__name__,
                    )
                else:
                    # Retry provider throttling and temporary server failures. A
                    # permanent 4xx response should be surfaced immediately.
                    if not response.is_error or (
                        response.status_code != 429 and response.status_code < 500
                    ):
                        break
                    logger.warning(
                        "Mailjet invitation attempt %s/%s returned status %s for user email domain %s",
                        attempt + 1,
                        MAILJET_SEND_ATTEMPTS,
                        response.status_code,
                        to_email.rsplit("@", 1)[-1],
                    )
                if attempt < MAILJET_SEND_ATTEMPTS - 1:
                    await asyncio.sleep(MAILJET_RETRY_DELAYS_SECONDS[attempt])

        if response is None:
            if last_transport_error is not None:
                raise last_transport_error
            return EmailDeliveryResult(False, error="The email provider is temporarily unavailable.")
        if response.is_error:
            logger.warning(
                "Mailjet rejected an Authenticator invitation for user email domain %s with status %s",
                to_email.rsplit("@", 1)[-1],
                response.status_code,
            )
            return EmailDeliveryResult(False, error="The email provider rejected the invitation.")

        data = response.json()
        message = (data.get("Messages") or [{}])[0]
        if str(message.get("Status", "")).lower() != "success":
            logger.warning(
                "Mailjet returned an unsuccessful Authenticator invitation result for user email domain %s",
                to_email.rsplit("@", 1)[-1],
            )
            return EmailDeliveryResult(False, error="The email provider rejected the invitation.")
        recipient = (message.get("To") or [{}])[0]
        raw_message_id = recipient.get("MessageID") or recipient.get("MessageUUID")
        message_id = str(raw_message_id) if raw_message_id is not None else None
        if message_id:
            try:
                async with httpx.AsyncClient(timeout=5) as history_client:
                    history_response = await history_client.get(
                        f"https://api.mailjet.com/v3/REST/messagehistory/{message_id}",
                        auth=httpx.BasicAuth(
                            settings.MAILJET_API_KEY, settings.MAILJET_SECRET_KEY
                        ),
                    )
                if not history_response.is_error:
                    events = history_response.json().get("Data") or []
                    failed_event = next(
                        (
                            item
                            for item in events
                            if str(item.get("EventType", "")).lower()
                            in {"blocked", "bounce", "spam"}
                        ),
                        None,
                    )
                    if failed_event:
                        state = str(failed_event.get("State") or "blocked").replace("_", " ")
                        logger.warning(
                            "Mailjet did not deliver an invitation for user email domain %s: %s",
                            to_email.rsplit("@", 1)[-1],
                            state,
                        )
                        return EmailDeliveryResult(
                            False,
                            provider_message_id=message_id,
                            error=(
                                "Mailjet blocked this recipient before delivery. "
                                "Check the address and Mailjet's blocked contacts list."
                            ),
                        )
            except (httpx.HTTPError, TypeError, ValueError):
                # Mailjet's event record can lag behind the accepted send response.
                # In that case the accepted response remains the best available result.
                pass
        return EmailDeliveryResult(True, provider_message_id=message_id)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Authenticator invitation email failed for user email domain %s: %s: %s",
            to_email.rsplit("@", 1)[-1],
            type(exc).__name__,
            str(exc),
        )
        return EmailDeliveryResult(False, error="The email provider is temporarily unavailable.")
