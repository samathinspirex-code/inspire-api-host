import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.errors import APIError
from app.modules.auth import service


class RefreshTokenRotationTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_parallel_reuse_does_not_revoke_new_sessions(self):
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
        user_repo = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(is_active=True)))
        expected = object()
        with (
            patch.object(service, "RefreshTokenRepository", return_value=refresh_repo),
            patch.object(service, "UserRepository", return_value=user_repo),
            patch.object(service, "_issue_tokens", AsyncMock(return_value=expected)),
        ):
            result = await service.refresh_tokens(AsyncMock(), "shared-token")
        self.assertIs(result, expected)
        refresh_repo.revoke_all_for_user.assert_not_awaited()

    async def test_reuse_outside_grace_revokes_user_sessions(self):
        now = datetime.now(timezone.utc)
        token = SimpleNamespace(
            user_id=7,
            expires_at=now + timedelta(days=1),
            revoked_at=now - timedelta(seconds=120),
        )
        refresh_repo = SimpleNamespace(
            get_by_hash=AsyncMock(return_value=token),
            revoke=AsyncMock(),
            revoke_all_for_user=AsyncMock(),
        )
        with patch.object(service, "RefreshTokenRepository", return_value=refresh_repo):
            with self.assertRaises(APIError):
                await service.refresh_tokens(AsyncMock(), "replayed-token")
        refresh_repo.revoke_all_for_user.assert_awaited_once_with(7)


if __name__ == "__main__":
    unittest.main()
