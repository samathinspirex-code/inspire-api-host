import csv
import unittest
from decimal import Decimal
from io import StringIO

from app.modules.lms.gradebook_service import build_gradebook_csv, percentage
from app.modules.lms.schemas import (
    GradebookAssignmentSummary,
    GradebookResultItem,
    GradebookStudentItem,
    GradebookSummary,
    LecturerGradebookResponse,
)


class GradebookTests(unittest.TestCase):
    def test_percentage_supports_weighted_marks_and_zero_total(self):
        self.assertEqual(percentage(Decimal("17.5"), Decimal("25")), 70.0)
        self.assertEqual(percentage(10, 0), 0.0)

    def test_export_has_separate_columns_and_blocks_spreadsheet_formulas(self):
        report = LecturerGradebookResponse(
            course_id=1,
            course_code="CYB",
            course_title="Cyber Security",
            class_id=None,
            class_label=None,
            summary=GradebookSummary(
                student_count=1,
                assignment_count=1,
                submitted_count=1,
                graded_count=1,
                average_percentage=80,
            ),
            assignments=[GradebookAssignmentSummary(
                assignment_id=2,
                title="Assessment 1",
                target_label="Entire course",
                max_marks=Decimal("50"),
                due_at=None,
                grades_released=True,
                eligible_students=1,
                submitted_count=1,
                graded_count=1,
                average_percentage=80,
            )],
            students=[GradebookStudentItem(
                student_user_id=3,
                student_number="=FORMULA",
                student_name="Student",
                student_email="student@example.com",
                graded_count=1,
                earned_marks=Decimal("40"),
                total_possible_marks=Decimal("50"),
                overall_percentage=80,
                results=[GradebookResultItem(
                    assignment_id=2,
                    title="Assessment 1",
                    status="reviewed",
                    max_marks=Decimal("50"),
                    marks_awarded=Decimal("40"),
                    percentage=80,
                    graded=True,
                )],
            )],
        )

        rows = list(csv.reader(StringIO(build_gradebook_csv(report))))

        self.assertEqual(len(rows[0]), 11)
        self.assertEqual(len(rows[1]), 11)
        self.assertEqual(rows[1][2], "'=FORMULA")
        self.assertEqual(rows[1][7:10], ["40", "50", "80.0"])
        self.assertEqual(rows[1][10], "True")


if __name__ == "__main__":
    unittest.main()
