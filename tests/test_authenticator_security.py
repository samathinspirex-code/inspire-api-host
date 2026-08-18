import unittest

from app.modules.auth.security import (
    _totp_code,
    generate_recovery_codes,
    generate_totp_secret,
    normalize_recovery_code,
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


if __name__ == "__main__":
    unittest.main()
