import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.errors import APIError
from app.modules.auth import service
from app.modules.auth.security import (
    _totp_code,
    generate_recovery_codes,
    generate_totp_secret,
    hash_password,
    normalize_recovery_code,
    verify_password,
    verify_totp,
)


class AuthenticatorSecurityTests(unittest.TestCase):
    def test_generated_totp_verifies_and_cannot_be_replayed(self):
        secret = generate_totp_secret()
        now = 1_700_000_000.0
        step = int(now // 30)
        code = _totp_code(secret, step)

        self.assertEqual(verify_totp(secret, code, now=now), step)
        self.assertIsNone(verify_totp(secret, code, last_used_step=step, now=now))

    def test_totp_allows_one_step_clock_skew(self):
        secret = generate_totp_secret()
        now = 1_700_000_000.0
        step = int(now // 30)

        self.assertEqual(verify_totp(secret, _totp_code(secret, step - 1), now=now), step - 1)
        self.assertEqual(verify_totp(secret, _totp_code(secret, step + 1), now=now), step + 1)

    def test_recovery_codes_are_unique_and_normalized(self):
        codes = generate_recovery_codes()

        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        self.assertTrue(all(len(normalize_recovery_code(code)) == 12 for code in codes))
        self.assertEqual(normalize_recovery_code("abcd-efgh-jklm"), "ABCDEFGHJKLM")

    def test_password_hash_is_salted_and_verifies_without_storing_plaintext(self):
        first = hash_password("SafeStudentPass123")
        second = hash_password("SafeStudentPass123")

        self.assertNotEqual(first, second)
        self.assertNotIn("SafeStudentPass123", first)
        self.assertTrue(verify_password("SafeStudentPass123", first))
        self.assertFalse(verify_password("WrongStudentPass123", first))
        self.assertFalse(verify_password("SafeStudentPass123", "not-a-valid-hash"))

    def test_password_access_is_limited_to_student_only_lms_accounts(self):
        def user_with(*keys):
            return SimpleNamespace(access_levels=[
                SimpleNamespace(access_level=SimpleNamespace(access_key=key, is_active=True))
                for key in keys
            ])

        self.assertTrue(service._student_password_eligible(user_with("LMS", "STUDENT")))
        self.assertFalse(service._student_password_eligible(user_with("LMS", "STUDENT", "ADMIN")))
        self.assertFalse(service._student_password_eligible(user_with("LMS", "LECTURER")))

    def test_student_password_strength_rules(self):
        service._validate_student_password("SafeStudentPass123", "learner@example.test")
        for password in ("short1", "letterswithoutdigits", "123456789012", "learnerPass123"):
            with self.subTest(password=password):
                with self.assertRaises(APIError) as raised:
                    service._validate_student_password(password, "learner@example.test")
                self.assertEqual(raised.exception.code, "PASSWORD_WEAK")


class CmsCommonLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_common_credential_issues_cms_user_tokens(self):
        user = SimpleNamespace(
            user_id=12,
            email="shared@example.test",
            is_active=True,
            access_levels=[SimpleNamespace(access_level=SimpleNamespace(access_key="CMS", is_active=True))],
        )
        repository = SimpleNamespace(get_by_email=AsyncMock(return_value=user))
        with (
            patch.object(service.settings, "CMS_COMMON_LOGIN_EMAIL", "shared@example.test"),
            patch.object(service.settings, "CMS_COMMON_LOGIN_CODE", "654321"),
            patch.object(service, "UserRepository", return_value=repository),
            patch.object(service, "_issue_tokens", AsyncMock(return_value="tokens")) as issue,
        ):
            result = await service.verify_cms_login(None, "SHARED@example.test", "654321", "local")

        self.assertEqual(result, "tokens")
        issue.assert_awaited_once_with(None, user)

    async def test_regular_cms_account_keeps_authenticator_login(self):
        with (
            patch.object(service.settings, "CMS_COMMON_LOGIN_EMAIL", "shared@example.test"),
            patch.object(service.settings, "CMS_COMMON_LOGIN_CODE", "654321"),
            patch.object(service, "verify_authenticator", AsyncMock(return_value="authenticator")) as verify,
        ):
            result = await service.verify_cms_login(None, "admin@example.test", "123456", "local")

        self.assertEqual(result, "authenticator")
        verify.assert_awaited_once_with(None, "admin@example.test", "123456", "local")


if __name__ == "__main__":
    unittest.main()
