import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.lms import assistant_service as service
from app.modules.lms.schemas import LectureQuizAnswerRequest, LectureQuizSubmitRequest


def scalar(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar_one.return_value = value
    result.one_or_none.return_value = value
    return result


class LectureQuizFeedbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.attempt = SimpleNamespace(attempt_id=10, learning_item_id=20,
            student_user_id=30, submitted_at=None, total_questions=2, score=None)
        self.rows = [(SimpleNamespace(position=i, selected_option=None, is_correct=None),
            SimpleNamespace(question_id=i, question=f"Concept question {i}", correct_option="B",
                explanation="B applies the course concept.", option_a="A text", option_b="B text",
                option_c="C text", option_d="D text")) for i in (1, 2)]
        self.access = patch.object(service, "_student_item", AsyncMock(return_value=SimpleNamespace(item_type="video")))
        self.access.start()
        self.addCleanup(self.access.stop)

    def db(self, attempt=None):
        rows = MagicMock()
        rows.all.return_value = self.rows
        return SimpleNamespace(execute=AsyncMock(side_effect=[scalar(attempt or self.attempt), rows]), commit=AsyncMock())

    def answer(self, question_id=1, selected_option="B"):
        return LectureQuizAnswerRequest(attempt_id=10, question_id=question_id, selected_option=selected_option)

    def submission(self, first="B", second="A"):
        return LectureQuizSubmitRequest(attempt_id=10, answers=[
            {"question_id": 1, "selected_option": first}, {"question_id": 2, "selected_option": second}])

    async def test_correct_answer_is_committed_before_feedback_and_attempt_is_locked(self):
        db = self.db()
        result = await service.answer_quiz_question(db, 20, self.answer(), 30)
        self.assertTrue(result.is_correct)
        self.assertEqual(result.correct_option, "B")
        self.assertEqual(self.rows[0][0].selected_option, "B")
        self.assertIsNone(self.attempt.submitted_at)
        db.commit.assert_awaited_once()
        self.assertIn("FOR UPDATE", str(db.execute.call_args_list[0].args[0]))

    async def test_wrong_answer_reveals_correct_option_but_keeps_original_choice(self):
        result = await service.answer_quiz_question(self.db(), 20, self.answer(selected_option="A"), 30)
        self.assertFalse(result.is_correct)
        self.assertEqual(result.selected_option, "A")
        self.assertEqual(result.correct_option, "B")

    async def test_duplicate_answer_is_idempotent_but_changed_answer_is_rejected(self):
        self.rows[0][0].selected_option = "A"
        self.rows[0][0].is_correct = False
        result = await service.answer_quiz_question(self.db(), 20, self.answer(selected_option="A"), 30)
        self.assertFalse(result.is_correct)
        db = self.db()
        with self.assertRaises(ValidationError):
            await service.answer_quiz_question(db, 20, self.answer(), 30)
        db.commit.assert_not_awaited()
        self.assertEqual(self.rows[0][0].selected_option, "A")

    async def test_cannot_skip_a_question_or_answer_an_unassigned_question(self):
        for question_id, error in [(2, ValidationError), (99, NotFoundError)]:
            with self.subTest(question_id=question_id), self.assertRaises(error):
                await service.answer_quiz_question(self.db(), 20, self.answer(question_id), 30)
        self.assertIsNone(self.rows[1][0].selected_option)

    async def test_cannot_access_another_students_or_another_videos_attempt(self):
        for field in ["student_user_id", "learning_item_id"]:
            attempt = SimpleNamespace(**vars(self.attempt))
            setattr(attempt, field, 99)
            with self.subTest(field=field), self.assertRaises(NotFoundError):
                await service.answer_quiz_question(self.db(attempt), 20, self.answer(), 30)

    async def test_course_access_is_checked(self):
        with patch.object(service, "_student_item", AsyncMock(side_effect=ForbiddenError("Unavailable"))):
            db = self.db()
            with self.assertRaises(ForbiddenError):
                await service.answer_quiz_question(db, 20, self.answer(), 30)
            db.execute.assert_not_awaited()

    async def test_final_submission_records_score_and_can_be_retried(self):
        self.rows[0][0].selected_option = "B"
        self.rows[0][0].is_correct = True
        result = await service.submit_quiz_attempt(self.db(), 20, self.submission(), 30)
        self.assertEqual(result.score, 1)
        self.assertEqual(result.total_questions, 2)
        self.assertIsNotNone(self.attempt.submitted_at)
        submitted_at = self.attempt.submitted_at
        retry = await service.submit_quiz_attempt(self.db(), 20, self.submission(), 30)
        self.assertEqual(result, retry)
        self.assertEqual(self.attempt.submitted_at, submitted_at)

    async def test_final_submission_cannot_replace_an_answer_after_feedback(self):
        self.rows[0][0].selected_option = "A"
        self.rows[0][0].is_correct = False
        db = self.db()
        with self.assertRaises(ValidationError):
            await service.submit_quiz_attempt(db, 20, self.submission(), 30)
        self.assertIsNone(self.rows[1][0].selected_option)
        self.assertIsNone(self.attempt.submitted_at)
        db.commit.assert_not_awaited()

    async def test_final_submission_requires_every_question_once(self):
        for answers in [[{"question_id": 1, "selected_option": "B"}],
                        [{"question_id": 1, "selected_option": "B"},
                         {"question_id": 2, "selected_option": "A"},
                         {"question_id": 2, "selected_option": "B"}]]:
            with self.subTest(answers=answers), self.assertRaises(ValidationError):
                await service.submit_quiz_attempt(self.db(), 20,
                    LectureQuizSubmitRequest(attempt_id=10, answers=answers), 30)

    async def test_resume_returns_feedback_only_for_answered_questions(self):
        self.rows[0][0].selected_option = "A"
        self.rows[0][0].is_correct = False
        rows = MagicMock()
        rows.all.return_value = self.rows
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            scalar(SimpleNamespace(is_completed=True)), scalar((self.attempt, 1)), rows]), commit=AsyncMock())
        result = await service.get_or_create_quiz_attempt(db, 20, 30)
        self.assertEqual(result.attempt_id, 10)
        self.assertEqual([answer.question_id for answer in result.answered_questions], [1])
        self.assertFalse(result.answered_questions[0].is_correct)
        self.assertEqual(len(result.questions), 2)
        for question in result.questions:
            self.assertNotIn("correct_option", question.model_dump())
            self.assertNotIn("explanation", question.model_dump())


if __name__ == "__main__":
    unittest.main()
