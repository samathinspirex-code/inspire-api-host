import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pydantic import ValidationError as PydanticValidationError

from app.modules.lms.content_service import is_practice_test_module
from app.modules.lms.schemas.exam import ExamScheduleUpdate, PracticeTestCreate


class PracticeTestIndependenceTests(unittest.TestCase):
    def test_system_practice_section_is_recognized_without_case_or_space_dependency(self):
        self.assertTrue(is_practice_test_module(SimpleNamespace(title=" Practice Test ")))
        self.assertTrue(is_practice_test_module(SimpleNamespace(title="PRACTICE TEST")))
        self.assertFalse(is_practice_test_module(SimpleNamespace(title="Week 1")))

    def test_independent_release_plan_requires_valid_dates_and_duration(self):
        start = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)
        plan = ExamScheduleUpdate(
            available_from=start,
            due_at=start + timedelta(days=1),
            duration_minutes=30,
        )
        self.assertEqual(plan.duration_minutes, 30)
        with self.assertRaises(PydanticValidationError):
            ExamScheduleUpdate(
                available_from=start,
                due_at=start,
                duration_minutes=30,
            )
        with self.assertRaises(PydanticValidationError):
            ExamScheduleUpdate(duration_minutes=0)

    def test_practice_test_can_be_scoped_to_one_class(self):
        payload = PracticeTestCreate(
            target_type="class",
            target_id=12,
            title="Class practice",
            instructions="Answer every question.",
        )
        self.assertEqual(payload.target_type, "class")
        self.assertEqual(payload.target_id, 12)


if __name__ == "__main__":
    unittest.main()
