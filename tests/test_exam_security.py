import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError as PydanticValidationError

from app.modules.lms.exam_service import fixed_exam_expiry, shuffled
from app.modules.lms.schemas import ExamQuestionUpsert
from app.modules.lms.schemas.exam import ExamAttemptQuestion


class ExamSecurityTests(unittest.TestCase):
    def test_attempt_deadline_is_fixed_from_first_start_and_capped_by_due_time(self):
        started = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
        due = started + timedelta(minutes=30)
        self.assertEqual(fixed_exam_expiry(started, 60, due), due)
        self.assertEqual(fixed_exam_expiry(started, 20, due), started + timedelta(minutes=20))

    def test_disabled_randomization_preserves_order(self):
        self.assertEqual(shuffled([3, 1, 2], False), [3, 1, 2])

    def test_mcq_requires_unique_options_and_valid_correct_answer(self):
        with self.assertRaises(PydanticValidationError):
            ExamQuestionUpsert(
                question_type="mcq", prompt="Choose one", marks=1,
                options=["Same", "same"], correct_option_index=0,
            )
        with self.assertRaises(PydanticValidationError):
            ExamQuestionUpsert(
                question_type="mcq", prompt="Choose one", marks=1,
                options=["A", "B"], correct_option_index=2,
            )

    def test_student_question_contract_never_contains_correct_answer(self):
        self.assertNotIn("correct_option_index", ExamAttemptQuestion.model_fields)
        self.assertNotIn("accepted_answers", ExamAttemptQuestion.model_fields)


if __name__ == "__main__":
    unittest.main()
