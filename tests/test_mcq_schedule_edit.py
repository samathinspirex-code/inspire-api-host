import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.errors import ForbiddenError, ValidationError
from app.modules.lms.coursework_service import (
    _validate_assignment_material, _validate_general_material, update_assignment,
)
from app.modules.lms.schemas.coursework import CourseworkAssignmentCreate


class McqScheduleEditTests(unittest.IsolatedAsyncioTestCase):
    def test_closed_assignment_can_be_saved_without_republishing(self):
        payload = CourseworkAssignmentCreate(
            course_id=8, target_type="course", target_id=8,
            title="Daily Quiz 01", instructions="Complete the quiz.",
            submission_type="mcq", duration_minutes=20, status="closed",
        )
        self.assertEqual(payload.status, "closed")

    async def test_edit_can_keep_existing_material_uploaded_by_another_lecturer(self):
        asset = SimpleNamespace(
            media_asset_id=9, status="ready", created_by=11,
            folder="assignment-materials", content_type="application/pdf",
        )
        db = SimpleNamespace(get=AsyncMock(return_value=asset))
        self.assertIs(await _validate_assignment_material(db, 9, 7, existing_asset_id=9), asset)
        self.assertIs(await _validate_general_material(db, 9, 7, existing_asset_id=9), asset)
        with self.assertRaises(ForbiddenError):
            await _validate_assignment_material(db, 9, 7, existing_asset_id=10)
        with self.assertRaises(ValidationError):
            await _validate_general_material(db, 9, 7, existing_asset_id=10)

    async def test_editing_due_date_updates_linked_exam_after_students_submitted(self):
        old_due = datetime(2026, 10, 17, 12, 29, tzinfo=timezone.utc)
        new_due = datetime(2026, 10, 23, 12, 29, tzinfo=timezone.utc)
        available = datetime(2026, 10, 11, 6, 30, tzinfo=timezone.utc)
        assignment = SimpleNamespace(
            assignment_id=52, course_id=8, target_type="course", target_id=8,
            title="Daily Quiz 01", instructions="Complete the quiz.",
            assignment_type="timed", submission_type="mcq", duration_minutes=20,
            available_from=available, due_at=old_due, status="published",
            question_paper_asset_id=None, material_asset_id=None,
        )
        exam = SimpleNamespace(
            exam_id=31, course_id=8, target_type="course", target_id=8,
            title=assignment.title, instructions=assignment.instructions,
            available_from=available, due_at=old_due, duration_minutes=20,
            status="published",
        )
        payload = CourseworkAssignmentCreate(
            course_id=8, target_type="course", target_id=8,
            title="Daily Quiz 01 revised", instructions="Complete the updated quiz.",
            assignment_type="timed", submission_type="mcq", duration_minutes=30,
            available_from=available, due_at=new_due, status="published",
        )
        db = SimpleNamespace(
            scalar=AsyncMock(side_effect=[4, exam]),
            commit=AsyncMock(), refresh=AsyncMock(),
        )
        repository = SimpleNamespace(get_assignment_context=AsyncMock(return_value=(assignment, None, None)))
        with patch("app.modules.lms.coursework_service.CourseworkRepository", return_value=repository), \
             patch("app.modules.lms.coursework_service._ensure_lecturer_course", new=AsyncMock()), \
             patch("app.modules.lms.coursework_service._validate_assignment_material", new=AsyncMock(return_value=None)), \
             patch("app.modules.lms.coursework_service._validate_general_material", new=AsyncMock(return_value=None)), \
             patch("app.modules.lms.coursework_service._assignment_material", new=AsyncMock(return_value=None)), \
             patch("app.modules.lms.coursework_service._assignment_item", return_value={"saved": True}):
            result = await update_assignment(db, 52, payload, 7)

        self.assertEqual(result, {"saved": True})
        self.assertEqual(assignment.due_at, new_due)
        self.assertEqual(exam.due_at, new_due)
        self.assertEqual(exam.available_from, available)
        self.assertEqual(assignment.status, "published")
        self.assertEqual(exam.status, "published")
        self.assertEqual(exam.duration_minutes, 30)
        self.assertEqual(exam.title, "Daily Quiz 01 revised")
        self.assertEqual(exam.instructions, "Complete the updated quiz.")
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
