import html
import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.core.config import settings
from app.modules.auth.schemas.auth import AuthenticatorPortalLink

logger = logging.getLogger(__name__)

MAILJET_SEND_URL = "https://api.mailjet.com/v3.1/send"


@dataclass(frozen=True)
class EmailDeliveryResult:
    sent: bool
    provider_message_id: str | None = None
    error: str | None = None


def _portal_links(setup_url: str, portal_links: list[AuthenticatorPortalLink] | None):
    return portal_links or [AuthenticatorPortalLink(portal="Inspire", setup_url=setup_url)]


def build_invitation_html(full_name: str, setup_url: str, expires_at: datetime, portal_links: list[AuthenticatorPortalLink] | None = None) -> str:
    safe_name = html.escape(full_name or "Inspire user")
    safe_expiry = html.escape(expires_at.strftime("%d %B %Y at %H:%M UTC"))
    links = _portal_links(setup_url, portal_links)
    setup_buttons = "".join(
        f'<p><a href="{html.escape(link.setup_url, quote=True)}">Set up Authenticator — {html.escape(link.portal)}</a></p>'
        for link in links
    )
    logins = " · ".join(
        f'<a href="{html.escape(link.login_url, quote=True)}">Open {html.escape(link.portal)}</a>'
        for link in links if link.login_url
    )
    guidance = ("Choose either portal link to set up Authenticator once. The same Authenticator works for both CMS and LMS. "
                "Completing setup uses the shared invitation, so both setup links become invalid.") if len(links) > 1 else "Use the secure link below to connect Google Authenticator."
    return f"""
    <div style="font-family:Arial,sans-serif;line-height:1.55;color:#202124;max-width:560px">
      <p>Hello {safe_name},</p>
      <p>Your Inspire College account is ready for Authenticator setup.</p>
      <p>{guidance}</p>
      {setup_buttons}
      <p>This single-use invitation expires on {safe_expiry}.</p>
      {f'<p>After setup, sign in using your Authenticator code: {logins}</p>' if logins else ''}
      <p>If you did not expect this account, contact your administrator. Do not forward this message or share its setup links.</p>
      <p>Inspire College</p>
    </div>
    """.strip()


def build_invitation_text(full_name: str, setup_url: str, expires_at: datetime, portal_links: list[AuthenticatorPortalLink] | None = None) -> str:
    links = _portal_links(setup_url, portal_links)
    setup_lines = "\n".join(f"{link.portal} — Set up Authenticator: {link.setup_url}" for link in links)
    logins = "\n".join(f"Open {link.portal}: {link.login_url}" for link in links if link.login_url)
    login_section = f"After setup, sign in using your Authenticator code:\n{logins}\n\n" if logins else ""
    guidance = ("Choose either portal link to set up Authenticator once. The same Authenticator works for both CMS and LMS. "
                "Completing setup uses the shared invitation, so both setup links become invalid.") if len(links) > 1 else "Use the secure link below to connect Google Authenticator."
    return (
        f"Hello {full_name or 'Inspire user'},\n\n"
        "Your Inspire College account is ready for Authenticator setup.\n\n"
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
) -> dict:
    return {
        "Messages": [
            {
                "From": {
                    "Email": settings.MAILJET_FROM_EMAIL,
                    "Name": settings.MAILJET_FROM_NAME,
                },
                "To": [{"Email": to_email, "Name": full_name}],
                "Subject": settings.AUTHENTICATOR_INVITATION_SUBJECT,
                "TextPart": build_invitation_text(full_name, setup_url, expires_at, portal_links),
                "HTMLPart": build_invitation_html(full_name, setup_url, expires_at, portal_links),
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
) -> EmailDeliveryResult:
    if not settings.MAILJET_API_KEY.strip() or not settings.MAILJET_SECRET_KEY.strip():
        return EmailDeliveryResult(False, error="The Mailjet API credentials are not configured.")
    if not settings.MAILJET_FROM_EMAIL.strip():
        return EmailDeliveryResult(False, error="The Mailjet sender email is not configured.")

    payload = build_mailjet_payload(
        to_email, full_name, setup_url, expires_at, idempotency_key, portal_links
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                MAILJET_SEND_URL,
                json=payload,
                auth=httpx.BasicAuth(
                    settings.MAILJET_API_KEY, settings.MAILJET_SECRET_KEY
                ),
            )
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
        return EmailDeliveryResult(True, provider_message_id=message_id)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Authenticator invitation email failed: %s", type(exc).__name__)
        return EmailDeliveryResult(False, error="The email provider is temporarily unavailable.")
