import html
import logging

import httpx

from app.core.config import settings
from app.modules.auth.invitation_email import EmailDeliveryResult, MAILJET_SEND_URL

logger = logging.getLogger(__name__)


def build_notification_payload(to_email: str, full_name: str, title: str, message: str, action_url: str | None, custom_id: str) -> dict:
    safe_name = html.escape(full_name or "Inspire user")
    safe_title = html.escape(title)
    safe_message = html.escape(message).replace("\n", "<br>")
    safe_url = html.escape(action_url or settings.LMS_UI_URL, quote=True)
    text_url = action_url or settings.LMS_UI_URL
    return {"Messages": [{
        "From": {"Email": settings.MAILJET_FROM_EMAIL, "Name": settings.MAILJET_FROM_NAME},
        "To": [{"Email": to_email, "Name": full_name}],
        "Subject": f"Inspire LMS: {title}",
        "TextPart": f"Hello {full_name or 'Inspire user'},\n\n{message}\n\nOpen Inspire LMS: {text_url}\n\nInspire College LMS",
        "HTMLPart": f'<div style="font-family:Arial,sans-serif;line-height:1.55;color:#202124;max-width:600px"><p>Hello {safe_name},</p><h2>{safe_title}</h2><p>{safe_message}</p><p><a href="{safe_url}" style="display:inline-block;padding:11px 18px;background:#57008b;color:#fff;text-decoration:none;border-radius:7px">Open Inspire LMS</a></p><p>Inspire College LMS</p></div>',
        "CustomID": custom_id,
        "TrackOpens": "disabled", "TrackClicks": "disabled",
    }]}


async def send_notification_email(to_email: str, full_name: str, title: str, message: str, action_url: str | None, idempotency_key: str) -> EmailDeliveryResult:
    if not settings.MAILJET_API_KEY.strip() or not settings.MAILJET_SECRET_KEY.strip():
        return EmailDeliveryResult(False, error="The Mailjet API credentials are not configured.")
    if not settings.MAILJET_FROM_EMAIL.strip():
        return EmailDeliveryResult(False, error="The Mailjet sender email is not configured.")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(MAILJET_SEND_URL,
                json=build_notification_payload(to_email, full_name, title, message, action_url, idempotency_key),
                auth=httpx.BasicAuth(settings.MAILJET_API_KEY, settings.MAILJET_SECRET_KEY))
        if response.is_error: return EmailDeliveryResult(False, error="The email provider rejected the notification.")
        item = (response.json().get("Messages") or [{}])[0]
        if str(item.get("Status", "")).lower() != "success": return EmailDeliveryResult(False, error="The email provider rejected the notification.")
        recipient = (item.get("To") or [{}])[0]
        raw_id = recipient.get("MessageID") or recipient.get("MessageUUID")
        return EmailDeliveryResult(True, provider_message_id=str(raw_id) if raw_id is not None else None)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("LMS notification email failed: %s", type(exc).__name__)
        return EmailDeliveryResult(False, error="The email provider is temporarily unavailable.")
