import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

from app.core.config import Settings, settings
from app.modules.auth import service
from app.modules.lms import service as lms_service
from app.core.errors import APIError
from app.modules.auth.repository.authenticator import AuthenticatorRepository
from app.modules.auth.invitation_email import (
    build_invitation_html,
    build_invitation_text,
    build_mailjet_payload,
    send_authenticator_invitation,
)
from app.modules.auth.service import build_authenticator_setup_url
from app.modules.auth.schemas.auth import AuthenticatorPortalLink, AuthenticatorSetupTokenResponse
from app.modules.user_management import service as user_management_service


class AuthenticatorInvitationTests(unittest.TestCase):
    def test_password_invitation_explains_student_password_setup(self):
        expiry = datetime(2026, 9, 4, tzinfo=timezone.utc)
        links = [AuthenticatorPortalLink(
            portal="LMS",
            setup_url="https://lms.example.test/?setup=password",
            login_url="https://lms.example.test",
        )]
        text = build_invitation_text(
            "Student", "https://lms.example.test/?setup=password", expiry,
            portal_links=links, setup_method="password",
        )
        html = build_invitation_html(
            "Student", "https://lms.example.test/?setup=password", expiry,
            portal_links=links, setup_method="password",
        )

        for body in (text, html):
            self.assertIn("password setup", body)
            self.assertIn("email address and password", body)
            self.assertNotIn("Google Authenticator", body)

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
    async def test_self_service_password_reset_keeps_current_password_until_completion(self):
        user = SimpleNamespace(
            user_id=25, email="student@example.test", full_name="Student", is_active=True,
            access_levels=[
                SimpleNamespace(access_level=SimpleNamespace(access_key=key, is_active=True))
                for key in ("LMS", "STUDENT")
            ],
        )
        setup = AuthenticatorSetupTokenResponse(
            user_id=25, email=user.email, setup_token="a" * 64,
            expires_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
        )
        issue = AsyncMock(return_value=setup)
        sender = AsyncMock()
        with (
            patch.object(settings, "LMS_UI_URL", "https://lms.example.test/"),
            patch.object(service, "check_ip_rate_limit"),
            patch.object(service, "UserRepository", return_value=SimpleNamespace(get_by_email=AsyncMock(return_value=user))),
            patch.object(service, "issue_authenticator_setup_token", issue),
            patch.object(service, "send_authenticator_invitation", sender),
        ):
            result = await service.request_password_reset(None, user.email, "lms", "127.0.0.1")

        issue.assert_awaited_once_with(
            None, 25, None, reset_credentials=False,
            expire_minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES,
        )
        self.assertEqual(sender.await_args.kwargs["setup_method"], "password_reset")
        self.assertEqual(result.message, "If an active account matches that email, a password reset link has been sent.")

    async def test_password_reset_does_not_reveal_unknown_accounts(self):
        issue = AsyncMock()
        sender = AsyncMock()
        with (
            patch.object(service, "check_ip_rate_limit"),
            patch.object(service, "UserRepository", return_value=SimpleNamespace(get_by_email=AsyncMock(return_value=None))),
            patch.object(service, "issue_authenticator_setup_token", issue),
            patch.object(service, "send_authenticator_invitation", sender),
        ):
            result = await service.request_password_reset(None, "missing@example.test", "lms", "127.0.0.1")

        issue.assert_not_awaited()
        sender.assert_not_awaited()
        self.assertEqual(result.message, "If an active account matches that email, a password reset link has been sent.")

    async def test_cms_created_student_receives_password_setup_not_authenticator(self):
        user = SimpleNamespace(access_levels=[
            SimpleNamespace(access_level=SimpleNamespace(access_key=key, is_active=True))
            for key in ("LMS", "STUDENT")
        ])
        password_invitation = AsyncMock(return_value=SimpleNamespace(setup_method="password"))
        authenticator_invitation = AsyncMock()
        with (
            patch.object(
                user_management_service,
                "UserManagementRepository",
                return_value=SimpleNamespace(get=AsyncMock(return_value=user)),
            ),
            patch.object(service, "issue_student_password_setup_invitation", password_invitation),
            patch.object(service, "issue_authenticator_setup_invitation", authenticator_invitation),
        ):
            result = await user_management_service.create_authenticator_setup_token(None, 25, 1)

        self.assertEqual(result.setup_method, "password")
        password_invitation.assert_awaited_once_with(None, 25, 1, settings.CMS_UI_URL)
        authenticator_invitation.assert_not_awaited()

    async def test_student_password_invitation_is_lms_only(self):
        user = SimpleNamespace(
            full_name="Student",
            access_levels=[
                SimpleNamespace(access_level=SimpleNamespace(access_key=key, is_active=True))
                for key in ("LMS", "STUDENT")
            ],
        )
        setup = AuthenticatorSetupTokenResponse(
            user_id=25,
            email="student@example.test",
            setup_token="a" * 64,
            expires_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
        )
        with (
            patch.object(settings, "LMS_UI_URL", "https://lms.example.test/"),
            patch.object(service, "UserRepository", return_value=SimpleNamespace(get=AsyncMock(return_value=user))),
            patch.object(service, "issue_authenticator_setup_token", AsyncMock(return_value=setup)),
            patch.object(service, "send_authenticator_invitation", AsyncMock(return_value=SimpleNamespace(sent=True))),
        ):
            result = await service.issue_student_password_setup_invitation(None, 25, 1)

        self.assertEqual(result.setup_method, "password")
        self.assertEqual([link.portal for link in result.portal_links], ["LMS"])
        self.assertEqual(parse_qs(urlparse(result.setup_url).query)["setup"], ["password"])

    async def test_password_is_allowed_when_account_has_privileged_access(self):
        for extra_access in ("LECTURER", "ADMIN", "SUPER_ADMIN", "CMS"):
            with self.subTest(extra_access=extra_access):
                user = SimpleNamespace(
                    user_id=25, email="staff@example.test", full_name="Staff", is_active=True,
                    access_levels=[
                        SimpleNamespace(access_level=SimpleNamespace(access_key=key, is_active=True))
                        for key in ("LMS", "STUDENT", extra_access)
                    ]
                )
                setup = AuthenticatorSetupTokenResponse(user_id=25, email=user.email, setup_token="a" * 64,
                                                       expires_at=datetime(2026, 9, 4, tzinfo=timezone.utc))
                with (
                    patch.object(service, "UserRepository", return_value=SimpleNamespace(get=AsyncMock(return_value=user))),
                    patch.object(service, "issue_authenticator_setup_token", AsyncMock(return_value=setup)),
                    patch.object(service, "send_authenticator_invitation", AsyncMock(return_value=SimpleNamespace(sent=True))),
                ):
                    result = await service.issue_student_password_setup_invitation(None, 25, 1)
                self.assertEqual(result.setup_method, "password")

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

    async def test_new_invitation_persists_and_emails_configured_expiry(self):
        now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
        expiry = now + timedelta(days=5)
        default_minutes = Settings.model_fields["AUTHENTICATOR_SETUP_EXPIRE_MINUTES"].default
        self.assertEqual(default_minutes, 7200)
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
            self.assertIn("05 September 2026 at 12:00 UTC", render(user.full_name, result.setup_url, expiry))

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

    async def test_mailjet_preblocked_result_is_not_reported_as_sent(self):
        send_response = MagicMock(is_error=False)
        send_response.json.return_value = {
            "Messages": [{"Status": "success", "To": [{"MessageID": 123456789}]}]
        }
        history_response = MagicMock(is_error=False)
        history_response.json.return_value = {
            "Data": [{"EventType": "blocked", "State": "preblocked"}]
        }
        send_client = MagicMock(post=AsyncMock(return_value=send_response))
        history_client = MagicMock(get=AsyncMock(return_value=history_response))
        send_context = MagicMock(
            __aenter__=AsyncMock(return_value=send_client),
            __aexit__=AsyncMock(return_value=None),
        )
        history_context = MagicMock(
            __aenter__=AsyncMock(return_value=history_client),
            __aexit__=AsyncMock(return_value=None),
        )

        with (
            patch.object(settings, "MAILJET_API_KEY", "public-key"),
            patch.object(settings, "MAILJET_SECRET_KEY", "secret-key"),
            patch.object(settings, "MAILJET_FROM_EMAIL", "lms@college.example"),
            patch(
                "app.modules.auth.invitation_email.httpx.AsyncClient",
                side_effect=[send_context, history_context],
            ),
        ):
            result = await send_authenticator_invitation(
                "student@example.com",
                "Example Student",
                "https://lms.example.com/setup",
                datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc),
                "password-setup-25",
                setup_method="password",
            )

        self.assertFalse(result.sent)
        self.assertIn("blocked this recipient", result.error)

    async def test_bulk_resend_only_targets_sent_and_expired_accounts(self):
        people = [
            SimpleNamespace(user_id=1, full_name="Sent", email="sent@example.com", is_active=True, authenticator_status="invitation_sent"),
            SimpleNamespace(user_id=2, full_name="Expired", email="expired@example.com", is_active=True, authenticator_status="invitation_expired"),
            SimpleNamespace(user_id=3, full_name="New", email="new@example.com", is_active=True, authenticator_status="not_invited"),
            SimpleNamespace(user_id=4, full_name="Configured", email="configured@example.com", is_active=True, authenticator_status="configured"),
            SimpleNamespace(user_id=5, full_name="Inactive", email="inactive@example.com", is_active=False, authenticator_status="invitation_sent"),
        ]
        sender = AsyncMock(side_effect=[
            SimpleNamespace(email_sent=True, delivery_message="sent"),
            SimpleNamespace(email_sent=False, delivery_message="blocked"),
        ])
        with (
            patch.object(lms_service, "list_students", AsyncMock(return_value=SimpleNamespace(data=people))),
            patch.object(lms_service, "send_person_authenticator_invitation", sender),
        ):
            result = await lms_service.resend_pending_password_invitations(None, "students", 99)

        self.assertEqual([call.args[1] for call in sender.await_args_list], [1, 2])
        self.assertEqual(result.eligible_count, 2)
        self.assertEqual(result.sent_count, 1)
        self.assertEqual(result.failed_count, 1)


if __name__ == "__main__":
    unittest.main()
