import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pydantic import ValidationError as PydanticValidationError

from app.modules.lms.exam_service import attempt_option_order, fixed_exam_expiry, shuffled
from app.modules.lms.schemas import ExamCreate, ExamQuestionUpsert
from app.modules.lms.schemas.exam import ExamAnswerUpdate, ExamAttemptQuestion


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

    def test_creation_rejects_whitespace_only_text(self):
        with self.assertRaises(PydanticValidationError):
            ExamCreate(
                course_id=1, target_id=1, title="  ", instructions="Valid instructions",
                duration_minutes=60,
            )
        with self.assertRaises(PydanticValidationError):
            ExamQuestionUpsert(
                question_type="essay", prompt="  ", marks=1,
            )
    def test_student_question_contract_never_contains_correct_answer(self):
        self.assertNotIn("correct_option_index", ExamAttemptQuestion.model_fields)
        self.assertNotIn("accepted_answers", ExamAttemptQuestion.model_fields)

    def test_multiple_answer_uses_stored_order_and_supports_older_attempts(self):
        question = SimpleNamespace(question_id=7, options=["A", "B", "C"])
        self.assertEqual(attempt_option_order(SimpleNamespace(option_orders={"7": [2, 0, 1]}), question), [2, 0, 1])
        self.assertEqual(attempt_option_order(SimpleNamespace(option_orders={}), question), [0, 1, 2])

    def test_multiple_answer_rejects_negative_indices_and_deduplicates(self):
        with self.assertRaises(PydanticValidationError):
            ExamAnswerUpdate(question_id=1, selected_option_indices=[-1])
        answer = ExamAnswerUpdate(question_id=1, selected_option_indices=[2, 1, 2])
        self.assertEqual(answer.selected_option_indices, [1, 2])


if __name__ == "__main__":
    unittest.main()
