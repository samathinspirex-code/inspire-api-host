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
        self.assertTrue(all(item.marks == Decimal("2") for item in questions))

    def test_rejects_missing_answer_without_partial_output(self):
        with self.assertRaisesRegex(ValidationError, "missing an Answer line"):
            parse_mcq_import("1. Incomplete?\nA. Yes\nB. No", Decimal("1"))
