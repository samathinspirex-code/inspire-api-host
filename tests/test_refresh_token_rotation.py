import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.errors import APIError
from app.modules.auth import service


class RefreshTokenSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_keeps_same_session_token(self):
        now = datetime.now(timezone.utc)
        token = SimpleNamespace(
            user_id=7,
            expires_at=now + timedelta(days=1),
            revoked_at=None,
        )
        refresh_repo = SimpleNamespace(
            get_by_hash=AsyncMock(return_value=token),
            revoke=AsyncMock(),
            revoke_all_for_user=AsyncMock(),
        )
        user = SimpleNamespace(
            user_id=7,
            email="student@example.com",
            full_name="Student Example",
            is_active=True,
            access_levels=[],
        )
        user_repo = SimpleNamespace(get=AsyncMock(return_value=user))
        with (
            patch.object(service, "RefreshTokenRepository", return_value=refresh_repo),
            patch.object(service, "UserRepository", return_value=user_repo),
            patch.object(service, "create_access_token", return_value=("new-access", 900)),
        ):
            result = await service.refresh_tokens(AsyncMock(), "shared-token")
        self.assertEqual(result.access_token, "new-access")
        self.assertEqual(result.refresh_token, "shared-token")
        refresh_repo.revoke.assert_not_awaited()
        refresh_repo.revoke_all_for_user.assert_not_awaited()

    async def test_revoked_token_is_rejected_without_revoking_other_sessions(self):
        now = datetime.now(timezone.utc)
        token = SimpleNamespace(
            user_id=7,
            expires_at=now + timedelta(days=1),
            revoked_at=now - timedelta(seconds=5),
        )
        refresh_repo = SimpleNamespace(
            get_by_hash=AsyncMock(return_value=token),
            revoke=AsyncMock(),
            revoke_all_for_user=AsyncMock(),
        )
        with patch.object(service, "RefreshTokenRepository", return_value=refresh_repo):
            with self.assertRaises(APIError):
                await service.refresh_tokens(AsyncMock(), "replayed-token")
        refresh_repo.revoke_all_for_user.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
