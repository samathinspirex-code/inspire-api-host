import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.core import public_form_email
from app.core.config import settings
from app.core.mailjet_smtp import SmtpDeliveryResult, _build_email
from app.modules.auth import invitation_email
from app.modules.lms import notification_email


def _failing_http_client():
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("connection reset"))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    return client, context


def test_smtp_message_contains_text_html_reply_to_and_custom_id():
    message = _build_email({
        "From": {"Email": "enrol@inspire.college", "Name": "Inspire College"},
        "To": [{"Email": "student@example.com", "Name": "Student"}],
        "ReplyTo": {"Email": "reply@example.com"},
        "Subject": "Welcome\nStudent",
        "TextPart": "Plain message",
        "HTMLPart": "<strong>HTML message</strong>",
        "CustomID": "setup-42",
    })

    assert message["Subject"] == "Welcome Student"
    assert message["Reply-To"] == "reply@example.com"
    assert message["X-MJ-CustomID"] == "setup-42"
    assert message.is_multipart()


async def _invitation_uses_smtp_after_mailjet_api_connection_reset():
    client, context = _failing_http_client()
    smtp = AsyncMock(return_value=SmtpDeliveryResult(True))
    with (
        patch.object(settings, "MAILJET_API_KEY", "public-key"),
        patch.object(settings, "MAILJET_SECRET_KEY", "secret-key"),
        patch.object(settings, "MAILJET_FROM_EMAIL", "enrol@inspire.college"),
        patch.object(invitation_email.httpx, "AsyncClient", return_value=context),
        patch.object(invitation_email.asyncio, "sleep", new=AsyncMock()),
        patch.object(invitation_email, "send_mailjet_smtp_message", smtp),
    ):
        result = await invitation_email.send_authenticator_invitation(
            "student@example.com",
            "Student",
            "https://lms.example/setup",
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            "setup-42",
            setup_method="password",
        )

    assert result.sent is True
    assert client.post.await_count == 3
    smtp.assert_awaited_once()


def test_invitation_uses_smtp_after_mailjet_api_connection_reset():
    asyncio.run(_invitation_uses_smtp_after_mailjet_api_connection_reset())


async def _notification_uses_smtp_after_mailjet_api_connection_reset():
    _, context = _failing_http_client()
    smtp = AsyncMock(return_value=SmtpDeliveryResult(True))
    with (
        patch.object(settings, "MAILJET_API_KEY", "public-key"),
        patch.object(settings, "MAILJET_SECRET_KEY", "secret-key"),
        patch.object(settings, "MAILJET_FROM_EMAIL", "enrol@inspire.college"),
        patch.object(notification_email.httpx, "AsyncClient", return_value=context),
        patch.object(notification_email, "send_mailjet_smtp_message", smtp),
    ):
        result = await notification_email.send_notification_email(
            "student@example.com", "Student", "New class", "Class details",
            "https://lms.example", "notification-42",
        )

    assert result.sent is True
    smtp.assert_awaited_once()


def test_notification_uses_smtp_after_mailjet_api_connection_reset():
    asyncio.run(_notification_uses_smtp_after_mailjet_api_connection_reset())


async def _public_form_uses_smtp_after_mailjet_api_connection_reset():
    _, context = _failing_http_client()
    smtp = AsyncMock(return_value=SmtpDeliveryResult(True))
    with (
        patch.object(settings, "MAILJET_API_KEY", "public-key"),
        patch.object(settings, "MAILJET_SECRET_KEY", "secret-key"),
        patch.object(settings, "PUBLIC_FORM_FROM_EMAIL", "enrol@inspire.college"),
        patch.object(settings, "PUBLIC_FORM_RECIPIENT_EMAIL", "enrol@inspire.college"),
        patch.object(public_form_email.httpx, "AsyncClient", return_value=context),
        patch.object(public_form_email, "send_mailjet_smtp_message", smtp),
    ):
        result = await public_form_email.send_public_form_email(
            "New enquiry", "Plain message", "<p>Message</p>",
            reply_to="visitor@example.com", custom_id="contact-42",
        )

    assert result.sent is True
    smtp.assert_awaited_once()


def test_public_form_uses_smtp_after_mailjet_api_connection_reset():
    asyncio.run(_public_form_uses_smtp_after_mailjet_api_connection_reset())
