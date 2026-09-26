import html
import logging

import httpx

from app.core.config import settings
from app.core.mailjet_smtp import send_mailjet_smtp_message
from app.modules.auth.invitation_email import EmailDeliveryResult, MAILJET_SEND_URL

logger = logging.getLogger(__name__)

MEETING_NOTIFICATION_TYPES = {"class_schedule", "class_reminder"}
MEETING_LABELS = (
    "Online class scheduled",
    "Online class rescheduled",
    "Online class cancelled",
    "Join online class now",
    "Online class in 24 hours",
    "Online class in 30 minutes",
    "Online class in 15 minutes",
)


def _subject(value: str) -> str:
    """Keep provider/browser subject lines compact and single-line."""
    clean = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return clean if len(clean) <= 120 else f"{clean[:117].rstrip()}..."


def _meeting_parts(title: str) -> tuple[str, str]:
    for label in MEETING_LABELS:
        prefix = f"{label}:"
        if title.startswith(prefix):
            return label, title[len(prefix):].strip()
    if ":" in title:
        label, event_title = title.split(":", 1)
        return label.strip(), event_title.strip()
    return "Online class update", title


def _meeting_button_label(status: str) -> str:
    lowered = status.lower()
    if "cancelled" in lowered:
        return "View class update"
    if "join" in lowered or "minutes" in lowered:
        return "Open and join class"
    return "View online class"


def _meeting_html(full_name: str, title: str, message: str, action_url: str) -> str:
    status, event_title = _meeting_parts(title)
    safe_name = html.escape(full_name or "Inspire user")
    safe_status = html.escape(status)
    safe_event_title = html.escape(event_title or "Your online class")
    safe_message = html.escape(message).replace("\n", "<br>")
    safe_url = html.escape(action_url, quote=True)
    button_label = html.escape(_meeting_button_label(status))
    is_cancelled = "cancelled" in status.lower()
    accent = "#B42318" if is_cancelled else "#65009B"
    badge_background = "#FEE4E2" if is_cancelled else "#F1E8F6"
    badge_text = "#B42318" if is_cancelled else "#65009B"
    preheader = html.escape(f"{status}: {event_title}")

    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F6F3F7;font-family:Arial,Helvetica,sans-serif;color:#24152C;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{preheader}</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background:#F6F3F7;">
    <tr><td align="center" style="padding:28px 12px;">
      <table role="presentation" width="620" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:620px;background:#FFFFFF;border:1px solid #E8DFEB;border-radius:16px;overflow:hidden;">
        <tr><td style="background:#27003D;padding:22px 32px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>
            <td style="font-size:18px;font-weight:700;letter-spacing:.3px;color:#FFFFFF;">INSPIRE <span style="color:#C997E2;">COLLEGE</span></td>
            <td align="right" style="font-size:12px;color:#DCCAE5;">Learning Management System</td>
          </tr></table>
        </td></tr>
        <tr><td style="padding:34px 32px 14px;">
          <span style="display:inline-block;padding:7px 11px;border-radius:999px;background:{badge_background};color:{badge_text};font-size:11px;font-weight:700;letter-spacing:.7px;text-transform:uppercase;">{safe_status}</span>
          <h1 style="margin:18px 0 10px;font-size:25px;line-height:1.28;color:#24152C;">{safe_event_title}</h1>
          <p style="margin:0;color:#736579;font-size:15px;line-height:1.6;">Hello {safe_name}, your online-class details are below.</p>
        </td></tr>
        <tr><td style="padding:12px 32px 8px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background:#FAF8FB;border:1px solid #E7DDEB;border-left:4px solid {accent};border-radius:10px;">
            <tr><td style="padding:20px 22px;">
              <div style="margin-bottom:7px;color:#8A7A90;font-size:11px;font-weight:700;letter-spacing:.7px;text-transform:uppercase;">Class details</div>
              <div style="color:#34213D;font-size:15px;line-height:1.65;">{safe_message}</div>
            </td></tr>
          </table>
        </td></tr>
        <tr><td style="padding:22px 32px 12px;">
          <table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr><td bgcolor="{accent}" style="border-radius:9px;">
            <a href="{safe_url}" style="display:inline-block;padding:13px 22px;color:#FFFFFF;font-size:15px;font-weight:700;text-decoration:none;border-radius:9px;">{button_label}</a>
          </td></tr></table>
          <p style="margin:18px 0 0;color:#8A7A90;font-size:12px;line-height:1.55;">Open the LMS for the latest meeting link and class details. Please do not forward your class access.</p>
        </td></tr>
        <tr><td style="padding:18px 32px 30px;">
          <div style="height:1px;background:#EEE7F0;margin-bottom:18px;"></div>
          <p style="margin:0 0 6px;color:#74667A;font-size:12px;line-height:1.5;">If the button does not work, copy this address into your browser:</p>
          <p style="margin:0;word-break:break-all;font-size:12px;line-height:1.5;"><a href="{safe_url}" style="color:#65009B;text-decoration:underline;">{safe_url}</a></p>
        </td></tr>
        <tr><td style="background:#F1ECF3;padding:17px 32px;color:#827487;font-size:11px;line-height:1.5;">Inspire College LMS · Automated class notification</td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _standard_html(full_name: str, title: str, message: str, action_url: str) -> str:
    safe_name = html.escape(full_name or "Inspire user")
    safe_title = html.escape(title)
    safe_message = html.escape(message).replace("\n", "<br>")
    safe_url = html.escape(action_url, quote=True)
    return f"""<!doctype html><html lang="en"><body style="margin:0;background:#F6F3F7;font-family:Arial,Helvetica,sans-serif;color:#24152C;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td align="center" style="padding:28px 12px;"><table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;background:#FFFFFF;border:1px solid #E8DFEB;border-radius:14px;overflow:hidden;">
<tr><td style="background:#27003D;padding:20px 28px;color:#FFFFFF;font-size:18px;font-weight:700;">INSPIRE <span style="color:#C997E2;">COLLEGE</span></td></tr>
<tr><td style="padding:30px 28px;"><p style="margin:0 0 14px;color:#736579;">Hello {safe_name},</p><h1 style="margin:0 0 14px;font-size:23px;line-height:1.3;">{safe_title}</h1><p style="margin:0 0 22px;color:#55465C;line-height:1.65;">{safe_message}</p><a href="{safe_url}" style="display:inline-block;padding:12px 20px;background:#65009B;color:#FFFFFF;text-decoration:none;border-radius:8px;font-weight:700;">Open Inspire LMS</a></td></tr>
<tr><td style="background:#F1ECF3;padding:16px 28px;color:#827487;font-size:11px;">Inspire College LMS · Automated notification</td></tr></table></td></tr></table></body></html>"""


def build_notification_payload(
    to_email: str,
    full_name: str,
    title: str,
    message: str,
    action_url: str | None,
    custom_id: str,
    notification_type: str | None = None,
) -> dict:
    url = action_url or settings.LMS_UI_URL
    is_meeting = notification_type in MEETING_NOTIFICATION_TYPES
    html_part = _meeting_html(full_name, title, message, url) if is_meeting else _standard_html(full_name, title, message, url)
    if is_meeting:
        status, event_title = _meeting_parts(title)
        subject = _subject(f"{status} · {event_title}")
        button_label = _meeting_button_label(status)
    else:
        subject = _subject(f"Inspire LMS: {title}")
        button_label = "Open Inspire LMS"
    return {"Messages": [{
        "From": {"Email": settings.MAILJET_FROM_EMAIL, "Name": settings.MAILJET_FROM_NAME},
        "To": [{"Email": to_email, "Name": full_name}],
        "Subject": subject,
        "TextPart": f"Hello {full_name or 'Inspire user'},\n\n{title}\n\n{message}\n\n{button_label}: {url}\n\nInspire College LMS",
        "HTMLPart": html_part,
        "CustomID": custom_id,
        "TrackOpens": "disabled", "TrackClicks": "disabled",
    }]}


async def send_notification_email(
    to_email: str,
    full_name: str,
    title: str,
    message: str,
    action_url: str | None,
    idempotency_key: str,
    notification_type: str | None = None,
) -> EmailDeliveryResult:
    if not settings.MAILJET_API_KEY.strip() or not settings.MAILJET_SECRET_KEY.strip():
        return EmailDeliveryResult(False, error="The Mailjet API credentials are not configured.")
    if not settings.MAILJET_FROM_EMAIL.strip():
        return EmailDeliveryResult(False, error="The Mailjet sender email is not configured.")
    payload = build_notification_payload(
        to_email, full_name, title, message, action_url, idempotency_key,
        notification_type=notification_type,
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                MAILJET_SEND_URL,
                json=payload,
                auth=httpx.BasicAuth(settings.MAILJET_API_KEY, settings.MAILJET_SECRET_KEY),
            )
        if response.is_error:
            if response.status_code == 429 or response.status_code >= 500:
                smtp_result = await send_mailjet_smtp_message(payload["Messages"][0])
                if smtp_result.sent:
                    return EmailDeliveryResult(True)
            return EmailDeliveryResult(False, error="The email provider rejected the notification.")
        item = (response.json().get("Messages") or [{}])[0]
        if str(item.get("Status", "")).lower() != "success":
            return EmailDeliveryResult(False, error="The email provider rejected the notification.")
        recipient = (item.get("To") or [{}])[0]
        raw_id = recipient.get("MessageID") or recipient.get("MessageUUID")
        return EmailDeliveryResult(True, provider_message_id=str(raw_id) if raw_id is not None else None)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("LMS notification email failed: %s", type(exc).__name__)
        smtp_result = await send_mailjet_smtp_message(payload["Messages"][0])
        return EmailDeliveryResult(
            smtp_result.sent,
            error=None if smtp_result.sent else "The email provider is temporarily unavailable.",
        )
