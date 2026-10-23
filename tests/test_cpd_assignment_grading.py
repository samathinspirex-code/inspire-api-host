import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine

from app.core.errors import ValidationError
from app.modules.lms import coursework_service
from app.modules.lms.assessment_grading import load_assignment_grading_modes
from app.modules.lms.schemas.coursework import CourseworkMarkUpdate


class CpdGradingResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_class_catalogue_overrides_course_and_cpd_uses_marks(self):
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            for sql in (
                "CREATE TABLE lms_coursework_assignments (assignment_id INTEGER, course_id INTEGER, target_type TEXT, target_id INTEGER)",
                "CREATE TABLE lms_courses (course_id INTEGER, catalogue_course_id INTEGER, program_id INTEGER)",
                "CREATE TABLE lms_classes (class_id INTEGER, academic_course_id INTEGER)",
                "CREATE TABLE academic_courses (course_id INTEGER, awarding_body TEXT)",
                "CREATE TABLE programs (program_id INTEGER, awarding_body TEXT)",
                "CREATE TABLE lms_exams (assignment_id INTEGER, assessment_kind TEXT)",
                "INSERT INTO academic_courses VALUES (1, 'ATHE'), (2, 'CPD')",
                "INSERT INTO programs VALUES (1, 'CPD')",
                "INSERT INTO lms_courses VALUES (10, 1, 1), (20, NULL, 1)",
                "INSERT INTO lms_classes VALUES (5, 2), (6, 1)",
                "INSERT INTO lms_coursework_assignments VALUES (1, 10, 'class', 5), (2, 10, 'class', 6), (3, 20, 'course', 20), (4, 10, 'course', 10), (5, 20, 'course', 20), (6, 10, 'class', 6)",
                "INSERT INTO lms_exams VALUES (5, 'exam'), (6, 'graded_mcq')",
            ):
                connection.exec_driver_sql(sql)

            async def execute(statement, params):
                return connection.execute(statement, params)

            assignments = [SimpleNamespace(assignment_id=i, submission_type="written" if i != 6 else "mcq") for i in range(1, 7)]
            await load_assignment_grading_modes(SimpleNamespace(execute=execute), assignments)
            self.assertEqual([item.grading_mode for item in assignments], ["marks", "bands", "marks", "bands", "bands", "bands"])
        engine.dispose()


class CpdMarkingTests(unittest.IsolatedAsyncioTestCase):
    async def mark(self, mode, payload):
        assignment = SimpleNamespace(assignment_id=4, course_id=1, submission_type="written", grading_mode=mode, max_marks=Decimal("100"))
        submission = SimpleNamespace(status="submitted")
        repo = MagicMock()
        repo.get_submission_context = AsyncMock(return_value=(submission, assignment))
        db = SimpleNamespace(commit=AsyncMock())
        with (patch.object(coursework_service, "CourseworkRepository", return_value=repo),
              patch.object(coursework_service, "_ensure_lecturer_course", new=AsyncMock()),
              patch.object(coursework_service, "list_submissions", new=AsyncMock())):
            await coursework_service.mark_submission(db, 7, payload, 9)
        return submission

    async def test_cpd_saves_exact_numeric_score_without_band(self):
        submission = await self.mark("marks", CourseworkMarkUpdate(marks_awarded=Decimal("78.25")))
        self.assertEqual(submission.marks_awarded, Decimal("78.25"))
        self.assertIsNone(submission.grade_band)

    async def test_cpd_rejects_band_only_marking(self):
        with self.assertRaises(ValidationError):
            await self.mark("marks", CourseworkMarkUpdate(grade_band="merit"))

    async def test_other_classes_keep_grade_bands(self):
        submission = await self.mark("bands", CourseworkMarkUpdate(grade_band="merit"))
        self.assertEqual(submission.grade_band, "merit")
        self.assertEqual(submission.marks_awarded, Decimal("65.00"))

    async def test_cpd_rejects_score_above_maximum(self):
        with self.assertRaises(ValidationError):
            await self.mark("marks", CourseworkMarkUpdate(marks_awarded=101))
