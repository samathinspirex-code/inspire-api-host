import io
import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openpyxl import load_workbook

from app.core.errors import ForbiddenError, ValidationError
from app.modules.lms import rubric_service
from app.modules.lms.rubric_excel import build_feedback_workbook, feedback_file_name, parse_rubric_workbook
from app.modules.lms.rubric_grading import compute_rubric_grade, grade_band, level_from_ac_code, normalise_level
from app.modules.lms.schemas.rubric import RubricAssessmentWrite, RubricCriterionResult

# Same structure as the ATHE Level 4 Business Environment sample rubric (no learner data).
SAMPLE = [
    ("LO1", "Understand types of organisation", "1.1", "pass"),
    ("LO1", "Understand types of organisation", "1.2", "pass"),
    ("LO1", "Understand types of organisation", "1.3", "pass"),
    ("LO1", "Understand types of organisation", "1M1", "merit"),
    ("LO2", "Understand the structure of organisations", "2.1", "pass"),
    ("LO2", "Understand the structure of organisations", "2.2", "pass"),
    ("LO2", "Understand the structure of organisations", "2D1", "distinction"),
    ("LO3", "Understand the market environment", "3.1", "pass"),
    ("LO3", "Understand the market environment", "3.2", "pass"),
    ("LO3", "Understand the market environment", "3M1", "merit"),
    ("LO4", "Understand the national environment", "4.1", "pass"),
    ("LO4", "Understand the national environment", "4.2", "pass"),
    ("LO4", "Understand the national environment", "4D1", "distinction"),
]
CRITERIA = [
    {"criterion_id": f"c{index}", "lo_code": lo, "lo_statement": statement, "ac_code": ac, "level": level, "statement": f"Criterion {ac}"}
    for index, (lo, statement, ac, level) in enumerate(SAMPLE, start=1)
]


def results(not_achieved=(), unmarked=()):
    return [
        {"criterion_id": item["criterion_id"], "achieved": None if item["ac_code"] in unmarked else item["ac_code"] not in not_achieved}
        for item in CRITERIA
    ]


class RubricGradingTests(unittest.TestCase):
    def test_sample_feedback_is_pass_because_merit_and_distinction_tasks_are_missing(self):
        summary = compute_rubric_grade(CRITERIA, results(not_achieved={"1M1", "2D1"}))
        self.assertEqual(summary["overall_grade"], "Pass")
        self.assertEqual(summary["all_pass"], "Achieved")
        self.assertEqual(summary["all_merit"], "Not yet")
        self.assertEqual(summary["all_distinction"], "Locked")
        self.assertTrue(summary["complete"])

    def test_all_criteria_give_distinction(self):
        self.assertEqual(compute_rubric_grade(CRITERIA, results())["overall_grade"], "Distinction")

    def test_merit_needs_all_pass_and_merit(self):
        summary = compute_rubric_grade(CRITERIA, results(not_achieved={"4D1"}))
        self.assertEqual(summary["overall_grade"], "Merit")
        self.assertEqual(summary["all_distinction"], "Not yet")

    def test_missing_pass_locks_merit_and_is_not_yet_achieved(self):
        summary = compute_rubric_grade(CRITERIA, results(not_achieved={"2.2"}))
        self.assertEqual(summary["overall_grade"], "Resubmission")
        self.assertEqual(summary["all_merit"], "Locked")
        self.assertEqual(grade_band(summary["overall_grade"]), "fail")
        lo2 = next(row for row in summary["groups"] if row["lo_code"] == "LO2")
        self.assertEqual(lo2["pass"], "Not yet")
        self.assertEqual(lo2["distinction"], "Locked")

    def test_no_merit_criteria_does_not_block_distinction(self):
        criteria = [item for item in CRITERIA if item["level"] != "merit"]
        summary = compute_rubric_grade(criteria, [{"criterion_id": item["criterion_id"], "achieved": True} for item in criteria])
        self.assertEqual(summary["all_merit"], "N/A")
        self.assertEqual(summary["overall_grade"], "Distinction")

    def test_unmarked_form_has_no_grade_and_partial_form_is_incomplete(self):
        self.assertIsNone(compute_rubric_grade(CRITERIA, [])["overall_grade"])
        self.assertFalse(compute_rubric_grade(CRITERIA, results(unmarked={"4D1"}))["complete"])

    def test_level_helpers(self):
        self.assertEqual(normalise_level("Distinction"), "distinction")
        self.assertEqual(normalise_level("M"), "merit")
        self.assertEqual(level_from_ac_code("1M1"), "merit")
        self.assertEqual(level_from_ac_code("2D1"), "distinction")
        self.assertEqual(level_from_ac_code("1.3"), "pass")


def sample_form(**overrides):
    criteria = [{**item, "achieved": item["ac_code"] not in {"1M1", "2D1"}, "evidence": "=SUM(A1)" if item["ac_code"] == "1.1" else "Evidence"} for item in CRITERIA]
    form = {
        "title": "ATHE LEVEL 4 HND IN BUSINESS AND MANAGEMENT",
        "subtitle": "Unit 1: The Business Environment – Assignment Feedback and Grading Rubric",
        "learner_name": "Test Learner",
        "learner_id": "IC000001",
        "qualification": "ATHE Level 4 HND in Business and Management",
        "unit_code_title": "Y/650/5055  The Business Environment",
        "assignment_title": "The Business Environment Assignment",
        "issued": date(2026, 5, 28),
        "submitted": date(2026, 7, 3),
        "resubmission": None,
        "first_assessor": "First Marker",
        "second_assessor": "Second Marker",
        "assessment_date": date(2026, 7, 13),
        "overall_grade": "Pass",
        "criteria": criteria,
        "actions_needed": "For 1M1: add the assessment\nFor 2D1: propose improvements",
        "overall_comment": "Good submission.",
    }
    form.update(overrides)
    return form


class RubricExcelTests(unittest.TestCase):
    def test_feedback_form_matches_sample_layout(self):
        sheet = load_workbook(io.BytesIO(build_feedback_workbook(sample_form()))).active
        self.assertEqual(sheet.title, "Marking Feedback")
        self.assertEqual(sheet["A4"].value, "Learner Name")
        self.assertEqual(sheet["B4"].value, "Test Learner")
        self.assertEqual(sheet["F6"].value, "28 May 2026")
        self.assertEqual(sheet["F9"].value, "Pass")
        self.assertEqual(sheet["C11"].value, "Assessment Criteria")
        self.assertEqual(sheet["C15"].value, "1M1")
        self.assertEqual(sheet["D15"].value, "Merit")
        self.assertEqual(sheet["F15"].value, "Not Achieved")
        self.assertEqual(sheet["G12"].value, "'=SUM(A1)")
        merges = {str(item) for item in sheet.merged_cells.ranges}
        self.assertTrue({"A1:G1", "A2:G2", "A12:A15", "B12:B15", "A16:A18", "F9:G9"} <= merges)
        self.assertEqual(sheet["A26"].value, "Actions needed to achieve LOs and meet the standards")
        self.assertEqual(sheet["A27"].value, "• For 1M1: add the assessment")

    def test_import_reads_criteria_and_header_and_ignores_learner_marks(self):
        content = build_feedback_workbook(sample_form())
        draft = parse_rubric_workbook(content, "Business Environment Rubric.xlsx")
        self.assertEqual(draft["name"], "Business Environment Rubric")
        self.assertEqual(draft["qualification"], "ATHE Level 4 HND in Business and Management")
        self.assertEqual(draft["unit_code_title"], "Y/650/5055  The Business Environment")
        self.assertEqual(draft["default_assignment_title"], "The Business Environment Assignment")
        self.assertEqual(draft["form_title"], "Unit 1: The Business Environment – Assignment Feedback and Grading Rubric")
        self.assertEqual([item["ac_code"] for item in draft["criteria"]], [item[2] for item in SAMPLE])
        self.assertEqual([item["level"] for item in draft["criteria"]], [item[3] for item in SAMPLE])
        self.assertEqual(draft["criteria"][4]["lo_code"], "LO2")
        self.assertEqual(draft["criteria"][5]["lo_statement"], "Understand the structure of organisations")
        self.assertNotIn("achieved", draft["criteria"][0])

    def test_import_rejects_unrelated_workbook(self):
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.active["A1"] = "Name"
        output = io.BytesIO()
        workbook.save(output)
        with self.assertRaises(ValidationError):
            parse_rubric_workbook(output.getvalue(), "x.xlsx")
        with self.assertRaises(ValidationError):
            parse_rubric_workbook(b"not a workbook", "x.xlsx")

    def test_file_name_is_safe(self):
        self.assertEqual(feedback_file_name("ATHE L4 Business Environment", "Test Learner"), "ATHE L4 Business Environment - Test Learner.xlsx")
        self.assertEqual(feedback_file_name("Unit 1/2: Rubric", "Ann", "2nd Assessor"), "Unit 1-2- Rubric - Ann - 2nd Assessor.xlsx")


class RubricVisibilityTests(unittest.TestCase):
    def test_second_assessor_cannot_see_first_form(self):
        self.assertFalse(rubric_service.can_read_form("second", "first"))
        self.assertTrue(rubric_service.can_read_form("first", "second"))
        self.assertTrue(rubric_service.can_read_form("admin", "first"))
        self.assertFalse(rubric_service.can_read_form("viewer", "second"))
        self.assertFalse(rubric_service.can_write_form("admin", "first"))
        self.assertTrue(rubric_service.can_write_form("second", "second"))

    def test_viewer_role(self):
        rubric = SimpleNamespace(first_assessor_user_id=1, second_assessor_user_id=2)
        self.assertEqual(rubric_service.viewer_role(rubric, 1, False), "first")
        self.assertEqual(rubric_service.viewer_role(rubric, 2, True), "second")
        self.assertEqual(rubric_service.viewer_role(rubric, 3, True), "admin")
        self.assertEqual(rubric_service.viewer_role(rubric, 3, False), "viewer")


class RubricSaveTests(unittest.IsolatedAsyncioTestCase):
    def context(self, released=False):
        submission = SimpleNamespace(submission_id=50, status="submitted", marks_awarded=None, grade_band=None, feedback=None, marked_by=None, marked_at=None)
        assignment = SimpleNamespace(assignment_id=7, course_id=1, max_marks=Decimal("100"), grades_released=released)
        rubric = SimpleNamespace(assignment_id=7, first_assessor_user_id=1, second_assessor_user_id=2, criteria_snapshot=CRITERIA)
        return submission, assignment, rubric

    async def save(self, user_id, form_role, payload, released=False, admin=False):
        submission, assignment, rubric = self.context(released)
        db = SimpleNamespace(add=MagicMock(), commit=AsyncMock())
        with (patch.object(rubric_service, "_submission_context", new=AsyncMock(return_value=(submission, assignment, rubric))),
              patch.object(rubric_service, "is_global_admin", new=AsyncMock(return_value=admin)),
              patch.object(rubric_service, "_forms", new=AsyncMock(return_value={})),
              patch.object(rubric_service, "get_marking", new=AsyncMock(return_value="ok"))):
            await rubric_service.save_assessment(db, 50, form_role, payload, user_id)
        return submission, db.add.call_args[0][0]

    def payload(self, complete=True, comment="Good work", **kwargs):
        return RubricAssessmentWrite(
            criteria_results=[RubricCriterionResult(**item) for item in results(**kwargs)],
            overall_comment=comment,
            complete=complete,
        )

    async def test_first_assessor_completion_sets_final_grade_on_submission(self):
        submission, form = await self.save(1, "first", self.payload(not_achieved={"1M1", "2D1"}))
        self.assertEqual(form.overall_grade, "Pass")
        self.assertEqual(form.status, "completed")
        self.assertEqual(submission.grade_band, "pass")
        self.assertEqual(submission.marks_awarded, Decimal("50.00"))
        self.assertEqual(submission.feedback, "Good work")
        self.assertEqual(submission.status, "reviewed")

    async def test_second_assessor_form_does_not_touch_submission(self):
        submission, form = await self.save(2, "second", self.payload())
        self.assertEqual(form.overall_grade, "Distinction")
        self.assertIsNone(submission.grade_band)

    async def test_second_assessor_cannot_write_first_form_and_admin_cannot_write_either(self):
        with self.assertRaises(ForbiddenError):
            await self.save(2, "first", self.payload())
        with self.assertRaises(ForbiddenError):
            await self.save(9, "first", self.payload(), admin=True)

    async def test_complete_requires_every_criterion_and_a_comment(self):
        with self.assertRaises(ValidationError):
            await self.save(1, "first", self.payload(unmarked={"4D1"}))
        with self.assertRaises(ValidationError):
            await self.save(1, "first", self.payload(comment=""))
        _, form = await self.save(1, "first", self.payload(complete=False, unmarked={"4D1"}))
        self.assertEqual(form.status, "draft")

    async def test_released_results_are_locked(self):
        with self.assertRaises(ValidationError):
            await self.save(1, "first", self.payload(), released=True)


class RubricReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def run_gate(self, completed_ids, user_id=1, admin=False):
        rubric = SimpleNamespace(first_assessor_user_id=1, second_assessor_user_id=2)
        submissions = [SimpleNamespace(submission_id=10), SimpleNamespace(submission_id=11)]
        calls = iter([submissions, completed_ids])

        async def execute(_statement):
            rows = next(calls)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

        db = SimpleNamespace(execute=execute)
        with (patch.object(rubric_service, "get_assignment_rubric", new=AsyncMock(return_value=rubric)),
              patch.object(rubric_service, "is_global_admin", new=AsyncMock(return_value=admin))):
            return await rubric_service.ensure_release_allowed(db, SimpleNamespace(assignment_id=7), user_id)

    async def test_release_blocked_until_every_first_form_is_complete(self):
        with self.assertRaises(ValidationError):
            await self.run_gate([10])
        self.assertTrue(await self.run_gate([10, 11]))

    async def test_only_first_assessor_or_admin_can_release(self):
        with self.assertRaises(ForbiddenError):
            await self.run_gate([10, 11], user_id=2)
        self.assertTrue(await self.run_gate([10, 11], user_id=5, admin=True))
