import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.user_management import service
from app.modules.user_management.repository.user import UserManagementRepository


class UserDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_revokes_account_without_removing_historical_user(self):
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), delete=AsyncMock())
        user = SimpleNamespace(
            user_id=24,
            email="samtest@example.com",
            full_name="Samtest",
            is_active=True,
            access_levels=[object()],
        )

        await UserManagementRepository(db).delete(user)

        self.assertFalse(user.is_active)
        self.assertEqual(user.email, "deleted+24@deleted.inspire.college")
        self.assertEqual(user.full_name, "Deleted user 24")
        self.assertEqual(user.access_levels, [])
        self.assertEqual(db.execute.await_count, 6)
        db.commit.assert_awaited_once()
        db.delete.assert_not_awaited()

    async def test_delete_clears_crm_assignment_and_commits_once(self):
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        user = SimpleNamespace(user_id=24)
        with patch.object(UserManagementRepository, "get", new_callable=AsyncMock, return_value=user), patch.object(
            UserManagementRepository, "delete", new_callable=AsyncMock
        ) as revoke:
            await service.delete_user(db, 24)

        self.assertEqual(db.execute.await_count, 2)
        revoke.assert_awaited_once_with(user)
        db.commit.assert_not_awaited()
