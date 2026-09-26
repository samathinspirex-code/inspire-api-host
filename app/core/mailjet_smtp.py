import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmtpDeliveryResult:
    sent: bool
    error: str | None = None


def _mailbox(value: dict) -> str:
    return formataddr((str(value.get("Name") or ""), str(value.get("Email") or "")))


def _build_email(message: dict) -> EmailMessage:
    email = EmailMessage()
    email["From"] = _mailbox(message["From"])
    email["To"] = ", ".join(_mailbox(recipient) for recipient in message["To"])
    email["Subject"] = " ".join(str(message["Subject"]).splitlines())
    if message.get("ReplyTo"):
        email["Reply-To"] = _mailbox(message["ReplyTo"])
    if message.get("CustomID"):
        email["X-MJ-CustomID"] = str(message["CustomID"])[:100]
    email.set_content(str(message.get("TextPart") or ""))
    if message.get("HTMLPart"):
        email.add_alternative(str(message["HTMLPart"]), subtype="html")
    return email


def _send_sync(message: dict) -> None:
    host = settings.MAILJET_SMTP_HOST.strip()
    port = settings.MAILJET_SMTP_PORT
    email = _build_email(message)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as client:
            client.login(settings.MAILJET_API_KEY, settings.MAILJET_SECRET_KEY)
            client.send_message(email)
        return
    with smtplib.SMTP(host, port, timeout=20) as client:
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
        client.login(settings.MAILJET_API_KEY, settings.MAILJET_SECRET_KEY)
        client.send_message(email)


async def send_mailjet_smtp_message(message: dict) -> SmtpDeliveryResult:
    if not settings.MAILJET_SMTP_FALLBACK_ENABLED:
        return SmtpDeliveryResult(False, "Mailjet SMTP fallback is disabled")
    if not settings.MAILJET_SMTP_HOST.strip():
        return SmtpDeliveryResult(False, "Mailjet SMTP host is not configured")
    try:
        await asyncio.to_thread(_send_sync, message)
        logger.info("Mailjet SMTP fallback accepted an email")
        return SmtpDeliveryResult(True)
    except (OSError, smtplib.SMTPException, ValueError, KeyError) as exc:
        logger.warning("Mailjet SMTP fallback failed: %s: %s", type(exc).__name__, str(exc))
        return SmtpDeliveryResult(False, "Mailjet SMTP delivery failed")
