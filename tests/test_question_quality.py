import unittest

from app.modules.lms.assistant_service import validate_generated_question


def candidate(question: str, **overrides):
    value = {
        "question": question,
        "option_a": "Encapsulation",
        "option_b": "Compilation",
        "option_c": "Replication",
        "option_d": "Compression",
        "correct_option": "A",
        "explanation": "Encapsulation groups state and behaviour behind a controlled interface.",
    }
    value.update(overrides)
    return value


class GeneratedQuestionQualityTests(unittest.TestCase):
    def test_accepts_a_course_concept_question(self):
        valid, reason = validate_generated_question(candidate(
            "Which object-oriented principle controls access to an object's internal state?"
        ), set())
        self.assertTrue(valid, reason)

    def test_rejects_lecturer_name_trivia(self):
        valid, _ = validate_generated_question(candidate(
            "Who was the lecturer name mentioned during this introductory video?"
        ), set())
        self.assertFalse(valid)

    def test_rejects_lecture_meta_wording(self):
        valid, _ = validate_generated_question(candidate(
            "According to the speaker in this video, which option was mentioned first?"
        ), set())
        self.assertFalse(valid)

    def test_rejects_duplicate_options(self):
        valid, _ = validate_generated_question(candidate(
            "Which principle provides controlled access to internal object state?",
            option_b="Encapsulation",
        ), set())
        self.assertFalse(valid)

    def test_rejects_duplicate_question(self):
        seen = set()
        value = candidate("Which principle provides controlled access to internal object state?")
        self.assertTrue(validate_generated_question(value, seen)[0])
        self.assertFalse(validate_generated_question(value, seen)[0])


if __name__ == "__main__":
    unittest.main()
