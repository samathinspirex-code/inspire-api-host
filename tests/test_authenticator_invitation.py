import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

from app.core.config import Settings, settings
from app.modules.auth import service
from app.modules.auth.repository.authenticator import AuthenticatorRepository
from app.modules.auth.invitation_email import (
    build_invitation_html,
    build_invitation_text,
    build_mailjet_payload,
    send_authenticator_invitation,
)
from app.modules.auth.service import build_authenticator_setup_url
from app.modules.auth.schemas.auth import AuthenticatorPortalLink, AuthenticatorSetupTokenResponse


class AuthenticatorInvitationTests(unittest.TestCase):
    def test_dual_portal_email_has_separate_links_and_shared_setup_guidance(self):
        links = [
            AuthenticatorPortalLink(portal=portal, setup_url=f"https://{portal.lower()}.example.test/?token=shared", login_url=f"https://{portal.lower()}.example.test")
            for portal in ["CMS", "LMS"]
        ]
        payload = build_mailjet_payload("user@example.test", "Example", links[0].setup_url,
                                       datetime(2026, 9, 2, tzinfo=timezone.utc), "test", links)
        self.assertEqual(len(payload["Messages"]), 1)
        for body in [payload["Messages"][0]["TextPart"], payload["Messages"][0]["HTMLPart"]]:
            for link in links:
                self.assertIn(link.setup_url, body)
                self.assertIn(f"Open {link.portal}", body)
            self.assertIn("set up Authenticator once", body)
            self.assertIn("both setup links become invalid", body)

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
    async def test_portal_links_follow_active_access_and_share_one_invitation(self):
        cases = [
            ([('CMS', True), ('LMS', True)], ['CMS', 'LMS']),
            ([('CMS', True)], ['CMS']),
            ([('LMS', True)], ['LMS']),
            ([('CMS', True), ('LMS', False)], ['CMS']),
            ([('CMS', False), ('LMS', True)], ['LMS']),
            ([('USER_MANAGEMENT', True), ('LMS', True)], ['CMS', 'LMS']),
        ]
        setup = AuthenticatorSetupTokenResponse(user_id=25, email="user@example.test", setup_token="a" * 64,
                                               expires_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
        for grants, expected in cases:
            with self.subTest(grants=grants):
                user = SimpleNamespace(full_name="Example", access_levels=[
                    SimpleNamespace(access_level=SimpleNamespace(access_key=key, is_active=active))
                    for key, active in grants
                ])
                issue = AsyncMock(return_value=setup)
                sender = AsyncMock(return_value=SimpleNamespace(sent=True))
                with (
                    patch.object(settings, "CMS_UI_URL", "https://cms.example.test/"),
                    patch.object(settings, "LMS_UI_URL", "https://lms.example.test/"),
                    patch.object(service, "issue_authenticator_setup_token", issue),
                    patch.object(service, "UserRepository", return_value=SimpleNamespace(get=AsyncMock(return_value=user))),
                    patch.object(service, "send_authenticator_invitation", sender),
                ):
                    result = await service.issue_authenticator_setup_invitation(None, 25, 1, "https://cms.example.test/")
                issue.assert_awaited_once_with(None, 25, 1)
                sender.assert_awaited_once()
                self.assertEqual([link.portal for link in result.portal_links], expected)
                self.assertEqual(result.setup_url, result.portal_links[0].setup_url)
                self.assertEqual(result.expires_at, setup.expires_at)
                self.assertEqual(sender.await_args.kwargs["portal_links"], result.portal_links)
                for link in result.portal_links:
                    self.assertEqual(link.login_url, f"https://{link.portal.lower()}.example.test")
                    parsed = urlparse(link.setup_url)
                    self.assertEqual(parsed.netloc, f"{link.portal.lower()}.example.test")
                    self.assertEqual(parse_qs(parsed.query)["token"], [setup.setup_token])
                    self.assertEqual(parse_qs(parsed.query)["email"], [setup.email])

    async def test_new_invitation_persists_and_emails_two_day_expiry(self):
        now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
        expiry = now + timedelta(days=2)
        default_minutes = Settings.model_fields["AUTHENTICATOR_SETUP_EXPIRE_MINUTES"].default
        self.assertEqual(default_minutes, 2880)
        user = SimpleNamespace(user_id=25, email="student@example.test", full_name="Student", is_active=True, access_levels=[])
        users = SimpleNamespace(get=AsyncMock(return_value=user))
        tokens = SimpleNamespace(create_setup_token=AsyncMock())
        sessions = SimpleNamespace(revoke_all_for_user=AsyncMock())
        sender = AsyncMock(return_value=SimpleNamespace(sent=True))
        with (
            patch.object(settings, "AUTHENTICATOR_SETUP_EXPIRE_MINUTES", default_minutes),
            patch.object(service, "datetime") as clock,
            patch.object(service, "UserRepository", return_value=users),
            patch.object(service, "AuthenticatorRepository", return_value=tokens),
            patch.object(service, "RefreshTokenRepository", return_value=sessions),
            patch.object(service, "send_authenticator_invitation", sender),
        ):
            clock.now.return_value = now
            result = await service.issue_authenticator_setup_invitation(None, 25, 1, "https://lms.example.test")
        self.assertEqual(result.expires_at, expiry)
        self.assertEqual(tokens.create_setup_token.await_args.args[2], expiry)
        self.assertEqual(sender.await_args.args[3], expiry)
        sessions.revoke_all_for_user.assert_awaited_once_with(25)
        for render in [build_invitation_html, build_invitation_text]:
            self.assertIn("02 September 2026 at 12:00 UTC", render(user.full_name, result.setup_url, expiry))

    async def test_setup_link_is_valid_until_two_day_boundary_and_stays_single_use(self):
        issued = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
        token = SimpleNamespace(expires_at=issued + timedelta(days=2), used_at=None)
        user = SimpleNamespace(user_id=25)
        result = MagicMock()
        result.one_or_none.return_value = (token, user)
        db = SimpleNamespace(execute=AsyncMock(return_value=result), rollback=AsyncMock())
        repo = AuthenticatorRepository(db)
        with patch("app.modules.auth.repository.authenticator.datetime") as clock:
            for offset in [timedelta(hours=1), timedelta(hours=24), timedelta(days=2) - timedelta(seconds=1)]:
                clock.now.return_value = issued + offset
                self.assertEqual(await repo.get_valid_setup_token("test-hash", "student@example.test"), (token, user))
            for offset in [timedelta(days=2), timedelta(days=2, seconds=1)]:
                clock.now.return_value = issued + offset
                self.assertIsNone(await repo.get_valid_setup_token("test-hash", "student@example.test"))
            clock.now.return_value = issued + timedelta(hours=1)
            token.used_at = issued + timedelta(minutes=10)
            self.assertIsNone(await repo.get_valid_setup_token("test-hash", "student@example.test"))

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
        links = [AuthenticatorPortalLink(portal=portal, setup_url=f"https://{portal.lower()}.example.test/setup") for portal in ["CMS", "LMS"]]
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
                portal_links=links,
            )

        self.assertTrue(result.sent)
        self.assertEqual(result.provider_message_id, "123456789")
        client.post.assert_awaited_once()
        sent_message = client.post.await_args.kwargs["json"]["Messages"][0]
        for link in links:
            self.assertIn(link.setup_url, sent_message["HTMLPart"])
            self.assertIn(link.setup_url, sent_message["TextPart"])


if __name__ == "__main__":
    unittest.main()
