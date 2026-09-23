import csv
from collections import defaultdict
from decimal import Decimal
from io import StringIO

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.modules.auth.models import User
from app.modules.lms.coursework_service import _assignment_item, _ensure_lecturer_course
from app.modules.lms.models import (
    ClassStudent,
    CourseEnrollment,
    LmsClass,
    LmsCourse,
    LmsCourseworkAssignment,
    LmsCourseworkSubmission,
    LmsExam,
    StudentProfile,
)
from app.modules.lms.repository import CourseworkRepository
from app.modules.lms.schemas import (
    GradebookAssignmentSummary,
    GradebookResultItem,
    GradebookStudentItem,
    GradebookSummary,
    LecturerGradebookResponse,
    StudentCourseGrade,
    StudentGradeItem,
    StudentGradesResponse,
)
from app.modules.lms import notification_service


def percentage(earned, possible) -> float:
    possible_value = Decimal(possible or 0)
    if possible_value <= 0:
        return 0.0
    return round(float(Decimal(earned or 0) / possible_value * 100), 2)


async def lecturer_gradebook(
    db: AsyncSession,
    user_id: int,
    course_id: int,
    class_id: int | None = None,
    assignment_id: int | None = None,
    search: str | None = None,
) -> LecturerGradebookResponse:
    await _ensure_lecturer_course(db, course_id, user_id)
    course = await db.get(LmsCourse, course_id)
    if course is None:
        raise NotFoundError("Course not found")
    selected_class = None
    if class_id is not None:
        selected_class = await db.get(LmsClass, class_id)
        if selected_class is None or selected_class.course_id != course_id:
            raise ValidationError("The selected class does not belong to this course")

    assignment_stmt = select(LmsCourseworkAssignment, LmsClass).outerjoin(
        LmsClass,
        and_(
            LmsCourseworkAssignment.target_type == "class",
            LmsClass.class_id == LmsCourseworkAssignment.target_id,
        ),
    ).where(
        LmsCourseworkAssignment.course_id == course_id,
        LmsCourseworkAssignment.status.in_(("published", "closed")),
    )
    if class_id is not None:
        assignment_stmt = assignment_stmt.where(or_(
            LmsCourseworkAssignment.target_type == "course",
            and_(LmsCourseworkAssignment.target_type == "class", LmsCourseworkAssignment.target_id == class_id),
        ))
    if assignment_id is not None:
        assignment_stmt = assignment_stmt.where(LmsCourseworkAssignment.assignment_id == assignment_id)
    assignment_rows = list((await db.execute(assignment_stmt.order_by(LmsCourseworkAssignment.created_at))).all())
    assignments = [row[0] for row in assignment_rows]

    student_stmt = (
        select(User, StudentProfile)
        .join(StudentProfile, StudentProfile.user_id == User.user_id)
        .join(CourseEnrollment, CourseEnrollment.student_user_id == User.user_id)
        .where(CourseEnrollment.course_id == course_id, CourseEnrollment.status == "enrolled")
    )
    if class_id is not None:
        student_stmt = student_stmt.join(
            ClassStudent,
            and_(ClassStudent.student_user_id == User.user_id, ClassStudent.class_id == class_id),
        )
    if search and search.strip():
        term = f"%{search.strip()}%"
        student_stmt = student_stmt.where(or_(
            User.full_name.ilike(term), User.email.ilike(term), StudentProfile.student_number.ilike(term)
        ))
    student_rows = list((await db.execute(student_stmt.order_by(User.full_name, User.email))).all())
    student_ids = [row[0].user_id for row in student_rows]

    class_map: dict[int, set[int]] = defaultdict(set)
    if student_ids:
        membership_rows = await db.execute(
            select(ClassStudent.student_user_id, ClassStudent.class_id)
            .join(LmsClass, LmsClass.class_id == ClassStudent.class_id)
            .where(LmsClass.course_id == course_id, ClassStudent.student_user_id.in_(student_ids))
        )
        for student_user_id, membership_class_id in membership_rows.all():
            class_map[student_user_id].add(membership_class_id)

    submission_map = {}
    if assignments and student_ids:
        submission_rows = await db.execute(
            select(LmsCourseworkSubmission).where(
                LmsCourseworkSubmission.assignment_id.in_([item.assignment_id for item in assignments]),
                LmsCourseworkSubmission.student_user_id.in_(student_ids),
            )
        )
        submission_map = {
            (item.assignment_id, item.student_user_id): item
            for item in submission_rows.scalars().all()
        }

    students = []
    for user, profile in student_rows:
        results = []
        earned = Decimal("0")
        possible = Decimal("0")
        graded_count = 0
        for assignment in assignments:
            eligible = assignment.target_type == "course" or assignment.target_id in class_map[user.user_id]
            if not eligible:
                continue
            submission = submission_map.get((assignment.assignment_id, user.user_id))
            marks = submission.marks_awarded if submission else None
            graded = marks is not None
            if graded:
                earned += Decimal(marks)
                possible += Decimal(assignment.max_marks)
                graded_count += 1
            results.append(GradebookResultItem(
                assignment_id=assignment.assignment_id,
                title=assignment.title,
                status=submission.status if submission else "not_started",
                max_marks=assignment.max_marks,
                marks_awarded=marks,
                percentage=percentage(marks, assignment.max_marks) if graded else None,
                graded=graded,
            ))
        students.append(GradebookStudentItem(
            student_user_id=user.user_id,
            student_number=profile.student_number,
            student_name=user.full_name or user.email,
            student_email=user.email,
            graded_count=graded_count,
            earned_marks=earned,
            total_possible_marks=possible,
            overall_percentage=percentage(earned, possible),
            results=results,
        ))

    assignment_summaries = []
    for assignment, target_class in assignment_rows:
        eligible_students = [
            item for item in students
            if any(result.assignment_id == assignment.assignment_id for result in item.results)
        ]
        relevant = [submission_map.get((assignment.assignment_id, item.student_user_id)) for item in eligible_students]
        submitted_count = sum(1 for item in relevant if item and item.status in {"submitted", "reviewed"})
        graded = [item for item in relevant if item and item.marks_awarded is not None]
        average = round(sum(percentage(item.marks_awarded, assignment.max_marks) for item in graded) / len(graded), 2) if graded else 0.0
        assignment_summaries.append(GradebookAssignmentSummary(
            assignment_id=assignment.assignment_id,
            title=assignment.title,
            target_label=target_class.name if target_class else "Entire course",
            max_marks=assignment.max_marks,
            due_at=assignment.due_at,
            grades_released=assignment.grades_released,
            eligible_students=len(eligible_students),
            submitted_count=submitted_count,
            graded_count=len(graded),
            average_percentage=average,
        ))

    all_submissions = list(submission_map.values())
    graded_students = [item for item in students if item.graded_count]
    return LecturerGradebookResponse(
        course_id=course.course_id,
        course_code=course.code,
        course_title=course.title,
        class_id=class_id,
        class_label=selected_class.name if selected_class else None,
        summary=GradebookSummary(
            student_count=len(students),
            assignment_count=len(assignments),
            submitted_count=sum(1 for item in all_submissions if item.status in {"submitted", "reviewed"}),
            graded_count=sum(1 for item in all_submissions if item.marks_awarded is not None),
            average_percentage=round(sum(item.overall_percentage for item in graded_students) / len(graded_students), 2) if graded_students else 0.0,
        ),
        assignments=assignment_summaries,
        students=students,
    )


async def student_grades(db: AsyncSession, user_id: int) -> StudentGradesResponse:
    rows = await CourseworkRepository(db).list_for_student(user_id, include_exams=True)
    grouped = {}
    for assignment, course, _target_class, submission in rows:
        if not assignment.grades_released or submission is None or submission.marks_awarded is None:
            continue
        group = grouped.setdefault(course.course_id, {
            "course": course, "earned": Decimal("0"), "possible": Decimal("0"), "grades": []
        })
        group["earned"] += Decimal(submission.marks_awarded)
        group["possible"] += Decimal(assignment.max_marks)
        group["grades"].append(StudentGradeItem(
            assignment_id=assignment.assignment_id,
            title=assignment.title,
            course_id=course.course_id,
            course_code=course.code,
            course_title=course.title,
            max_marks=assignment.max_marks,
            marks_awarded=submission.marks_awarded,
            percentage=percentage(submission.marks_awarded, assignment.max_marks),
            feedback=submission.feedback,
            marked_at=submission.marked_at,
        ))
    courses = [StudentCourseGrade(
        course_id=value["course"].course_id,
        course_code=value["course"].code,
        course_title=value["course"].title,
        released_assignments=len(value["grades"]),
        earned_marks=value["earned"],
        total_possible_marks=value["possible"],
        overall_percentage=percentage(value["earned"], value["possible"]),
        grades=value["grades"],
    ) for value in grouped.values()]
    earned = sum((item.earned_marks for item in courses), Decimal("0"))
    possible = sum((item.total_possible_marks for item in courses), Decimal("0"))
    return StudentGradesResponse(
        overall_percentage=percentage(earned, possible),
        released_assignments=sum(item.released_assignments for item in courses),
        courses=courses,
    )


async def set_grade_release(db: AsyncSession, assignment_id: int, released: bool, user_id: int):
    assignment = await CourseworkRepository(db).get_assignment(assignment_id)
    if assignment is None:
        raise NotFoundError("Assignment not found")
    await _ensure_lecturer_course(db, assignment.course_id, user_id)
    assignment.grades_released = released
    linked_exam = (await db.execute(
        select(LmsExam).where(LmsExam.assignment_id == assignment_id)
    )).scalar_one_or_none()
    if linked_exam is not None:
        linked_exam.grades_released = released
    await db.commit()
    if released:
        await notification_service.notify_grade_release(db, assignment)
    context = await CourseworkRepository(db).get_assignment_context(assignment_id)
    return _assignment_item(context)


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def build_gradebook_csv(report: LecturerGradebookResponse) -> str:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["course_code", "class", "student_number", "student_name", "student_email", "assignment", "status", "marks_awarded", "maximum_marks", "percentage", "grades_released"])
    assignment_map = {item.assignment_id: item for item in report.assignments}
    for student in report.students:
        for result in student.results:
            assignment = assignment_map[result.assignment_id]
            writer.writerow([_csv_safe(report.course_code), _csv_safe(report.class_label), _csv_safe(student.student_number), _csv_safe(student.student_name), _csv_safe(student.student_email), _csv_safe(result.title), _csv_safe(result.status), _csv_safe(result.marks_awarded), _csv_safe(result.max_marks), _csv_safe(result.percentage), _csv_safe(assignment.grades_released)])
    return output.getvalue()
