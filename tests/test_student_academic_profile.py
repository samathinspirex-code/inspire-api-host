import unittest

from app.core.errors import ForbiddenError
from app.modules.lms.profile_service import _ensure_student_profile_access


class ScalarDatabase:
    def __init__(self, values=()):
        self.values = iter(values)

    async def scalar(self, _statement):
        return next(self.values)


class StudentAcademicProfileAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_student_can_view_only_their_own_profile(self):
        await _ensure_student_profile_access(ScalarDatabase(), 20, 20, "STUDENT")
        with self.assertRaises(ForbiddenError):
            await _ensure_student_profile_access(ScalarDatabase(), 21, 20, "STUDENT")

    async def test_admin_can_view_any_student(self):
        await _ensure_student_profile_access(ScalarDatabase(), 20, 1, "ADMIN")
        await _ensure_student_profile_access(ScalarDatabase(), 20, 1, "SUPER_ADMIN")

    async def test_lecturer_must_teach_the_student(self):
        await _ensure_student_profile_access(ScalarDatabase([1]), 20, 10, "LECTURER")
        with self.assertRaises(ForbiddenError):
            await _ensure_student_profile_access(ScalarDatabase([0]), 20, 10, "LECTURER")


if __name__ == "__main__":
    unittest.main()
