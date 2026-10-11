import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.errors import ForbiddenError
from app.modules.lms import content_service, router
from app.modules.lms.schemas.content import CourseProgressionSettings


class LecturerCourseAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_progression_settings_checks_course_manager_before_saving(self):
        db = object()
        payload = CourseProgressionSettings(sequential_content_enabled=False)
        for role in ("ADMIN", "SUPER_ADMIN", "LECTURER"):
            user = SimpleNamespace(user_id=17, access=["LMS", role])
            with patch.object(content_service, "ensure_course_manager", new_callable=AsyncMock) as authorize, \
                 patch.object(content_service, "update_course_progression_settings", new_callable=AsyncMock) as save:
                await router.update_course_progression_settings(8, payload, user, db)
                authorize.assert_awaited_once_with(db, 8, 17)
                save.assert_awaited_once_with(db, 8, payload)
        with patch.object(content_service, "ensure_course_manager", side_effect=ForbiddenError("Not assigned")), \
             patch.object(content_service, "update_course_progression_settings", new_callable=AsyncMock) as save:
            with self.assertRaises(ForbiddenError):
                await router.update_course_progression_settings(8, payload, user, db)
            save.assert_not_awaited()

    async def test_student_role_cannot_change_progression_settings(self):
        with self.assertRaises(ForbiddenError):
            await router.lecturer_access(SimpleNamespace(user_id=23, access=["LMS", "STUDENT"]))

    async def test_progress_routes_pass_the_requesters_actual_role(self):
        db = object()
        for role in ("ADMIN", "SUPER_ADMIN", "LECTURER"):
            with self.subTest(role=role):
                current_user = SimpleNamespace(user_id=17, access=["LMS", role])
                with patch.object(router.progress_service, "get_course_progress", new_callable=AsyncMock) as detail:
                    await router.get_student_course_progress(8, 23, current_user, db)
                    detail.assert_awaited_once_with(db, 8, 23, 17, role)
                with patch.object(router.progress_service, "get_course_progress_summary", new_callable=AsyncMock) as summary:
                    await router.get_course_progress_summary(8, current_user, db)
                    summary.assert_awaited_once_with(db, 8, 17, role)

    async def test_class_lecturer_can_open_the_class_course(self):
        db = SimpleNamespace(
            get=AsyncMock(return_value=None),
            scalar=AsyncMock(return_value=42),
        )
        course_repo = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(source_master_course_id=None))
        )
        with patch.object(content_service, "CourseRepository", return_value=course_repo):
            await content_service._ensure_course_access(db, 8, 17, "LECTURER")
        db.scalar.assert_awaited_once()

    async def test_unassigned_lecturer_is_still_rejected(self):
        db = SimpleNamespace(
            get=AsyncMock(return_value=None),
            scalar=AsyncMock(return_value=None),
        )
        course_repo = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(source_master_course_id=None))
        )
        with patch.object(content_service, "CourseRepository", return_value=course_repo):
            with self.assertRaises(ForbiddenError):
                await content_service._ensure_course_access(db, 8, 17, "LECTURER")

    async def test_stale_direct_course_link_does_not_preserve_access(self):
        db = SimpleNamespace(
            get=AsyncMock(return_value=object()),
            scalar=AsyncMock(return_value=None),
        )
        course_repo = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(source_master_course_id=None))
        )
        with patch.object(content_service, "CourseRepository", return_value=course_repo):
            with self.assertRaises(ForbiddenError):
                await content_service._ensure_course_access(db, 8, 17, "LECTURER")
        db.scalar.assert_awaited_once()

    async def test_class_copy_also_checks_its_master_course_assignment(self):
        db = SimpleNamespace(
            get=AsyncMock(return_value=None),
            scalar=AsyncMock(return_value=42),
        )
        course_repo = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(source_master_course_id=3))
        )
        with patch.object(content_service, "CourseRepository", return_value=course_repo):
            await content_service._ensure_course_access(db, 8, 17, "LECTURER")

        statement = db.scalar.await_args.args[0]
        list_parameters = [value for value in statement.compile().params.values() if isinstance(value, list)]
        self.assertTrue(any(set(value) == {3, 8} for value in list_parameters))


if __name__ == "__main__":
    unittest.main()
