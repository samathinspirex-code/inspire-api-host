import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

from app.core.config import settings
from app.modules.auth.invitation_email import (
    build_invitation_html,
    build_mailjet_payload,
    send_authenticator_invitation,
)
from app.modules.auth.service import build_authenticator_setup_url


class AuthenticatorInvitationTests(unittest.TestCase):
    def test_setup_url_contains_encoded_email_and_single_use_token(self):
        token = "a" * 64
        result = build_authenticator_setup_url(
            "student+online@example.com", token, "https://lms.example.com/setup/"
        )

        parsed = urlparse(result)
        query = parse_qs(parsed.query)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", "https://lms.example.com/setup")
        self.assertEqual(query["setup"], ["authenticator"])
        self.assertEqual(query["email"], ["student+online@example.com"])
        self.assertEqual(query["token"], [token])

    def test_email_html_escapes_user_controlled_values(self):
        result = build_invitation_html(
            "<script>alert(1)</script>",
            'https://lms.example.com/?token="unsafe"&email=a@example.com',
            datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc),
        )

        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", result)
        self.assertIn("&quot;unsafe&quot;&amp;email=", result)

    def test_mailjet_payload_uses_transactional_v31_format(self):
        expires_at = datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc)
        with (
            patch.object(settings, "MAILJET_FROM_EMAIL", "lms@college.example"),
            patch.object(settings, "MAILJET_FROM_NAME", "Inspire College"),
            patch.object(
                settings,
                "AUTHENTICATOR_INVITATION_SUBJECT",
                "Set up Authenticator",
            ),
        ):
            result = build_mailjet_payload(
                "student@example.com",
                "Example Student",
                "https://lms.example.com/setup",
                expires_at,
                "authenticator-setup-25",
            )

        message = result["Messages"][0]
        self.assertEqual(
            message["From"],
            {"Email": "lms@college.example", "Name": "Inspire College"},
        )
        self.assertEqual(
            message["To"],
            [{"Email": "student@example.com", "Name": "Example Student"}],
        )
        self.assertEqual(message["Subject"], "Set up Authenticator")
        self.assertEqual(message["CustomID"], "authenticator-setup-25")
        self.assertEqual(message["TrackOpens"], "disabled")
        self.assertEqual(message["TrackClicks"], "disabled")
        self.assertIn("TextPart", message)
        self.assertIn("HTMLPart", message)


class AuthenticatorInvitationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_mailjet_credentials_returns_manual_fallback(self):
        with (
            patch.object(settings, "MAILJET_API_KEY", ""),
            patch.object(settings, "MAILJET_SECRET_KEY", ""),
        ):
            result = await send_authenticator_invitation(
                "student@example.com",
                "Example Student",
                "https://lms.example.com/setup",
                datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc),
                "authenticator-setup-25",
            )

        self.assertFalse(result.sent)
        self.assertIn("Mailjet API credentials", result.error)

    async def test_successful_mailjet_response_returns_message_id(self):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "Messages": [
                {
                    "Status": "success",
                    "To": [
                        {
                            "Email": "student@example.com",
                            "MessageID": 123456789,
                        }
                    ],
                }
            ]
        }
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        client_context = MagicMock()
        client_context.__aenter__ = AsyncMock(return_value=client)
        client_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(settings, "MAILJET_API_KEY", "public-key"),
            patch.object(settings, "MAILJET_SECRET_KEY", "secret-key"),
            patch.object(settings, "MAILJET_FROM_EMAIL", "lms@college.example"),
            patch(
                "app.modules.auth.invitation_email.httpx.AsyncClient",
                return_value=client_context,
            ),
        ):
            result = await send_authenticator_invitation(
                "student@example.com",
                "Example Student",
                "https://lms.example.com/setup",
                datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc),
                "authenticator-setup-25",
            )

        self.assertTrue(result.sent)
        self.assertEqual(result.provider_message_id, "123456789")
        client.post.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
