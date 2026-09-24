from decimal import Decimal
import unittest

from app.core.errors import ValidationError
from app.modules.lms.exam_service import parse_mcq_import


class ExamQuestionImportTests(unittest.TestCase):
    def test_accepts_letters_numbers_and_option_labels(self):
        questions = parse_mcq_import(
            """
            1. Capital of France?
            A. Paris
            B. Rome
            Answer: A

            Question 2: Two plus two?
            Option1: 3
            Option2: 4
            Answer: 2
            """,
            Decimal("2"),
        )

        self.assertEqual([item.prompt for item in questions], ["Capital of France?", "Two plus two?"])
        self.assertEqual([item.correct_option_index for item in questions], [0, 1])
        self.assertTrue(all(item.question_type == "mcq" for item in questions))
        self.assertTrue(all(item.marks == Decimal("2") for item in questions))

    def test_several_correct_options_become_a_multiple_answer_question(self):
        questions = parse_mcq_import(
            """
            1. Which are primary colours?
            A. Red
            B. Green
            C. Blue
            D. Pink
            Answers: A and C
            """,
            Decimal("1"),
        )

        self.assertEqual(questions[0].question_type, "multiple_answer")
        self.assertEqual(questions[0].correct_option_indices, [0, 2])

    def test_rejects_missing_answer_without_partial_output(self):
        with self.assertRaisesRegex(ValidationError, "missing an Answer line"):
            parse_mcq_import("1. Incomplete?\nA. Yes\nB. No", Decimal("1"))
