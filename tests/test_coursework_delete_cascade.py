import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.errors import ForbiddenError
from app.modules.lms import coursework_service


class CourseworkDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_attempted_assignment_can_be_deleted_with_related_notifications(self):
        assignment = SimpleNamespace(assignment_id=52, course_id=53)
        repo = MagicMock()
        repo.get_assignment_context = AsyncMock(return_value=(assignment,))
        db = MagicMock()
        db.scalar = AsyncMock(return_value=31)
        db.execute = AsyncMock()
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        with patch.object(coursework_service, "CourseworkRepository", return_value=repo), \
             patch.object(coursework_service, "_ensure_lecturer_course", new=AsyncMock()):
            await coursework_service.delete_assignment(db, 52, 6)

        statement = db.execute.await_args.args[0]
        sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("assignment:52:published", sql)
        self.assertIn("grade:exam:52:released", sql)
        self.assertIn("exam:31:due:%", sql)
        db.delete.assert_awaited_once_with(assignment)
        db.commit.assert_awaited_once()

    async def test_lecturer_without_course_access_cannot_delete(self):
        assignment = SimpleNamespace(assignment_id=52, course_id=53)
        repo = MagicMock()
        repo.get_assignment_context = AsyncMock(return_value=(assignment,))
        db = MagicMock()
        db.delete = AsyncMock()
        with patch.object(coursework_service, "CourseworkRepository", return_value=repo), \
             patch.object(coursework_service, "_ensure_lecturer_course", new=AsyncMock(
                 side_effect=ForbiddenError("No access")
             )):
            with self.assertRaises(ForbiddenError):
                await coursework_service.delete_assignment(db, 52, 6)
        db.delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
