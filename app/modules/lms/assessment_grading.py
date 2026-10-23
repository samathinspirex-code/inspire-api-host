from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession


def grading_mode(assignment) -> str:
    return getattr(assignment, "grading_mode", "bands")


async def load_assignment_grading_modes(db: AsyncSession, assignments) -> None:
    """Resolve class-specific awarding bodies without changing stored scores."""
    assignments = list(assignments)
    ids = list({item.assignment_id for item in assignments})
    if not ids:
        return
    rows = (await db.execute(text("""
        SELECT a.assignment_id,
               COALESCE(NULLIF(TRIM(ac.awarding_body), ''), pr.awarding_body, '') AS awarding_body,
               e.assessment_kind
        FROM lms_coursework_assignments a
        JOIN lms_courses c ON c.course_id = a.course_id
        LEFT JOIN lms_classes lc ON a.target_type = 'class' AND lc.class_id = a.target_id
        LEFT JOIN academic_courses ac ON ac.course_id = COALESCE(lc.academic_course_id, c.catalogue_course_id)
        LEFT JOIN programs pr ON pr.program_id = c.program_id
        LEFT JOIN lms_exams e ON e.assignment_id = a.assignment_id
        WHERE a.assignment_id IN :assignment_ids
    """).bindparams(bindparam("assignment_ids", expanding=True)), {"assignment_ids": ids})).all()
    bodies = {assignment_id: (body, kind) for assignment_id, body, kind in rows}
    for item in assignments:
        body, kind = bodies.get(item.assignment_id, ("", None))
        cpd_assignment = str(body or "").strip().casefold() == "cpd" and kind not in {"exam", "practice_test"}
        item.grading_mode = "marks" if cpd_assignment else "bands"
