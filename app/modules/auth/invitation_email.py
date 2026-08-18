import html
import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

MAILJET_SEND_URL = "https://api.mailjet.com/v3.1/send"


@dataclass(frozen=True)
class EmailDeliveryResult:
    sent: bool
    provider_message_id: str | None = None
    error: str | None = None


def build_invitation_html(full_name: str, setup_url: str, expires_at: datetime) -> str:
    safe_name = html.escape(full_name or "Inspire user")
    safe_url = html.escape(setup_url, quote=True)
    safe_expiry = html.escape(expires_at.strftime("%d %B %Y at %H:%M UTC"))
    return f"""
    <div style="font-family:Arial,sans-serif;line-height:1.55;color:#202124;max-width:560px">
      <p>Hello {safe_name},</p>
      <p>An administrator created an Inspire LMS account for this email address.</p>
      <p>Open the following secure link to connect Google Authenticator:</p>
      <p><a href="{safe_url}">Complete Authenticator setup</a></p>
      <p>The single-use link expires on {safe_expiry}.</p>
      <p>If you did not expect this account, contact your LMS administrator. Do not forward this message or share its setup link.</p>
      <p>Inspire College LMS</p>
    </div>
    """.strip()


def build_invitation_text(full_name: str, setup_url: str, expires_at: datetime) -> str:
    return (
        f"Hello {full_name or 'Inspire user'},\n\n"
        "An administrator created an Inspire LMS account for this email address.\n\n"
        "Open this secure link to connect Google Authenticator:\n"
        f"{setup_url}\n\n"
        f"This single-use link expires on {expires_at.strftime('%d %B %Y at %H:%M UTC')}.\n\n"
        "If you did not expect this account, contact your LMS administrator. "
        "Do not forward this message or share its setup link.\n\n"
        "Inspire College LMS"
    )


def build_mailjet_payload(
    to_email: str,
    full_name: str,
    setup_url: str,
    expires_at: datetime,
    custom_id: str,
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
                "TextPart": build_invitation_text(full_name, setup_url, expires_at),
                "HTMLPart": build_invitation_html(full_name, setup_url, expires_at),
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
) -> EmailDeliveryResult:
    if not settings.MAILJET_API_KEY.strip() or not settings.MAILJET_SECRET_KEY.strip():
        return EmailDeliveryResult(False, error="The Mailjet API credentials are not configured.")
    if not settings.MAILJET_FROM_EMAIL.strip():
        return EmailDeliveryResult(False, error="The Mailjet sender email is not configured.")

    payload = build_mailjet_payload(
        to_email, full_name, setup_url, expires_at, idempotency_key
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
