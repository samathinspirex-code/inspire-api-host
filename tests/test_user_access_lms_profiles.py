import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.lms.models import LecturerProfile, StudentProfile
from app.modules.user_management import service
from app.modules.user_management.repository.user import UserManagementRepository
from app.modules.user_management.schemas import UserUpdate


def fake_db(existing=()):
    added = []
    db = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, _id: object() if model in existing else None),
        add=MagicMock(side_effect=added.append),
        execute=AsyncMock(),
        commit=AsyncMock(),
    )
    return db, added


class LmsProfileOnAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_granting_student_access_creates_student_profile(self):
        db, added = fake_db()
        self.assertTrue(await service.ensure_lms_profiles(db, 7, ["LMS", "STUDENT"]))
        self.assertEqual([type(item) for item in added], [StudentProfile])
        self.assertEqual(added[0].user_id, 7)

    async def test_granting_lecturer_access_creates_lecturer_profile(self):
        db, added = fake_db()
        await service.ensure_lms_profiles(db, 7, ["LMS", "LECTURER"])
        self.assertEqual([type(item) for item in added], [LecturerProfile])

    async def test_existing_profile_and_other_access_are_left_alone(self):
        db, added = fake_db(existing=(StudentProfile,))
        self.assertFalse(await service.ensure_lms_profiles(db, 7, ["LMS", "STUDENT", "CMS"]))
        self.assertFalse(await service.ensure_lms_profiles(db, 7, ["CMS", "COUNSELLOR"]))
        self.assertEqual(added, [])

    async def test_update_user_adds_profile_for_newly_granted_access(self):
        db, added = fake_db()
        user = SimpleNamespace(user_id=7, full_name="Existing Staff", email="staff@example.com", access_levels=[])
        levels = [SimpleNamespace(access_key="LMS"), SimpleNamespace(access_key="LECTURER")]
        with patch.object(UserManagementRepository, "get", new_callable=AsyncMock, return_value=user), \
             patch.object(UserManagementRepository, "get_by_email", new_callable=AsyncMock, return_value=None), \
             patch.object(UserManagementRepository, "get_access_levels_by_keys", new_callable=AsyncMock, return_value=levels), \
             patch.object(UserManagementRepository, "update", new_callable=AsyncMock, return_value=user), \
             patch.object(service, "_to_detail", return_value="detail"), \
             patch("app.modules.user_management.service.AuthenticatorRepository") as auth:
            auth.return_value.configured_user_ids = AsyncMock(return_value=set())
            await service.update_user(db, 7, UserUpdate(name="Existing Staff", email="staff@example.com", access=["LMS", "LECTURER"]))
        self.assertEqual([type(item) for item in added], [LecturerProfile])
        db.commit.assert_awaited()
