"""Existing sessions must follow current account grants, including LMS role selection."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app.core.errors import ForbiddenError, UnauthorizedError
from app.modules.auth.dependencies import get_current_user


def account(keys, active=True):
    return SimpleNamespace(
        user_id=7, email="current@example.com", is_active=active,
        access_levels=[SimpleNamespace(access_level=SimpleNamespace(access_key=key, is_active=True)) for key in keys],
    )


class LiveAccountAccessTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, user, selected=None):
        headers = [(b"authorization", b"Bearer existing-token")]
        if selected:
            headers.append((b"x-lms-active-role", selected.encode()))
        request = Request({"type": "http", "headers": headers})
        with patch("app.modules.auth.dependencies.decode_access_token", return_value={
            "sub": "7", "email": "old@example.com", "access": ["USER_MANAGEMENT", "ADMIN"],
        }), patch("app.modules.auth.dependencies.UserRepository") as repository:
            repository.return_value.get = AsyncMock(return_value=user)
            return await get_current_user(request, db=AsyncMock())

    async def test_existing_token_uses_added_and_removed_grants(self):
        current = await self.resolve(account(["FINANCE"]))
        self.assertEqual(current.access, ["FINANCE"])
        self.assertEqual(current.email, "current@example.com")

    async def test_deleted_or_disabled_account_cannot_keep_session(self):
        for user in [None, account(["ADMIN"], active=False)]:
            with self.assertRaises(UnauthorizedError):
                await self.resolve(user)

    async def test_removed_selected_lms_role_is_denied(self):
        with self.assertRaises(ForbiddenError):
            await self.resolve(account(["LMS", "STUDENT"]), selected="ADMIN")

    async def test_lms_selection_still_limits_current_roles(self):
        current = await self.resolve(account(["LMS", "ADMIN", "STUDENT"]), selected="STUDENT")
        self.assertEqual(current.access, ["LMS", "STUDENT"])
