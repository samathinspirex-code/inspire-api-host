import html
import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings


logger = logging.getLogger(__name__)
MAILJET_SEND_URL = "https://api.mailjet.com/v3.1/send"


@dataclass(frozen=True)
class FormEmailResult:
    sent: bool
    error: str | None = None


def detail_table(rows: list[tuple[str, str | None]]) -> str:
    return "".join(
        "<tr>"
        f'<th style="padding:10px 14px;text-align:left;vertical-align:top;border-bottom:1px solid #e8dff0;color:#5c5266;width:180px">{html.escape(label)}</th>'
        f'<td style="padding:10px 14px;border-bottom:1px solid #e8dff0;color:#21162d">{html.escape(value or "—")}</td>'
        "</tr>"
        for label, value in rows
    )


def form_email_html(title: str, intro: str, rows: list[tuple[str, str | None]], message: str | None = None) -> str:
    message_block = ""
    if message:
        message_block = (
            '<div style="margin-top:22px;padding:18px;border-radius:10px;background:#f6f0fb">'
            '<strong style="display:block;margin-bottom:8px;color:#4b007d">Message</strong>'
            f'<div style="white-space:pre-wrap;color:#21162d">{html.escape(message)}</div></div>'
        )
    return (
        '<div style="font-family:Arial,sans-serif;line-height:1.55;color:#21162d;max-width:680px;margin:auto">'
        '<div style="padding:24px 28px;border-radius:14px 14px 0 0;background:#4b007d;color:#fff">'
        f'<div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;opacity:.8">Inspire College website</div><h1 style="margin:7px 0 0;font-size:25px">{html.escape(title)}</h1></div>'
        '<div style="padding:26px 28px;border:1px solid #e4d9ec;border-top:0;border-radius:0 0 14px 14px;background:#fff">'
        f'<p style="margin:0 0 20px;color:#5c5266">{html.escape(intro)}</p>'
        f'<table role="presentation" style="width:100%;border-collapse:collapse;border:1px solid #e8dff0;border-radius:10px">{detail_table(rows)}</table>'
        f'{message_block}<p style="margin:22px 0 0;color:#766d7d;font-size:12px">This notification was generated automatically by the Inspire College website.</p></div></div>'
    )


async def send_public_form_email(subject: str, text_body: str, html_body: str, reply_to: str | None = None, custom_id: str = "website-form") -> FormEmailResult:
    if not settings.MAILJET_API_KEY.strip() or not settings.MAILJET_SECRET_KEY.strip() or not settings.PUBLIC_FORM_FROM_EMAIL.strip():
        return FormEmailResult(False, "Mailjet is not configured")
    recipient = settings.PUBLIC_FORM_RECIPIENT_EMAIL.strip()
    if not recipient:
        return FormEmailResult(False, "The form recipient is not configured")
    message = {
        "From": {"Email": settings.PUBLIC_FORM_FROM_EMAIL, "Name": settings.PUBLIC_FORM_FROM_NAME},
        "To": [{"Email": recipient, "Name": "Inspire Admissions"}],
        "Subject": subject,
        "TextPart": text_body,
        "HTMLPart": html_body,
        "CustomID": custom_id[:100],
        "TrackOpens": "disabled",
        "TrackClicks": "disabled",
    }
    if reply_to:
        message["ReplyTo"] = {"Email": reply_to}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                MAILJET_SEND_URL,
                json={"Messages": [message]},
                auth=httpx.BasicAuth(settings.MAILJET_API_KEY, settings.MAILJET_SECRET_KEY),
            )
        if response.is_error:
            logger.warning("Mailjet rejected a public form notification with status %s", response.status_code)
            return FormEmailResult(False, "The email provider rejected the message")
        payload = response.json()
        if str(((payload.get("Messages") or [{}])[0]).get("Status", "")).lower() != "success":
            return FormEmailResult(False, "The email provider did not accept the message")
        return FormEmailResult(True)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Public form email failed: %s", type(exc).__name__)
        return FormEmailResult(False, "The email provider is temporarily unavailable")
