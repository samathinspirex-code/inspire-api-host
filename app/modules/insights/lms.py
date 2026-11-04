"""Student outcomes measured from LMS records.

Attendance uses the same present / all records rule as the LMS dashboard. Dropout
counts only enrolments the LMS marks as withdrawn, never inactivity. A student
counts as active in a month when they have any recorded learning activity in it.
"""
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_TTL = 300.0
_cache: tuple[float, dict] | None = None
MAX_INTAKES = 10
ACTIVITY_SQL = """
    SELECT r.student_user_id::bigint AS student, coalesce(s.actual_start_time, s.created_at) AS at
      FROM lms_attendance_records r JOIN lms_attendance_sessions s ON s.attendance_session_id = r.attendance_session_id
      WHERE r.status = 'present'
    UNION ALL SELECT student_user_id, last_activity_at FROM lms_learning_progress WHERE last_activity_at IS NOT NULL
    UNION ALL SELECT student_user_id, submitted_at FROM lms_coursework_submissions WHERE submitted_at IS NOT NULL
    UNION ALL SELECT student_user_id, submitted_at FROM lms_exam_attempts WHERE submitted_at IS NOT NULL
    UNION ALL SELECT student_user_id, submitted_at FROM lms_lecture_quiz_attempts WHERE submitted_at IS NOT NULL
"""


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def build_cohorts(intakes: list[tuple[int, date]], active: list[tuple[int, date]], today: date) -> list[dict]:
    """(student, first enrolment month) and (student, active month) -> % of each intake active N months on."""
    months: dict[int, set[int]] = {}
    for student, month in active:
        months.setdefault(student, set()).add(_month_index(month))
    groups: dict[int, list[int]] = {}
    for student, month in intakes:
        groups.setdefault(_month_index(month), []).append(student)
    current = _month_index(today)
    rows = []
    for start in sorted(groups)[-MAX_INTAKES:]:
        students = groups[start]
        cells = [round(sum(1 for student in students if start + step in months.get(student, ())) / len(students) * 100)
                 for step in range(min(10, current - start + 1))]
        rows.append({"intake": date(start // 12, start % 12 + 1, 1).strftime("%b %Y"), "students": len(students), "cells": cells})
    return rows


def summarize(counts: dict, cohorts: list[dict]) -> dict:
    enrolments = counts["enrolments"]
    records_30d = counts["records_30d"]
    records = counts["records"]
    enrolled = counts["enrolled_students"]
    return {
        "source": "cms",
        "connected": bool(records or enrolments),
        "receiving": bool(records_30d or counts["active_30d"]),
        "attendance": round(counts["present_30d"] / records_30d * 100, 1) if records_30d else None,
        "attendance_all": round(counts["present"] / records * 100, 1) if records else None,
        "records_30d": records_30d,
        "records": records,
        "sessions_30d": counts["sessions_30d"],
        "dropout": round(counts["withdrawn"] / enrolments * 100, 1) if enrolments else None,
        "withdrawn": counts["withdrawn"],
        "enrolments": enrolments,
        "enrolled_students": enrolled,
        "active_30d": counts["active_30d"],
        "inactive_share": round((enrolled - counts["active_30d"]) / enrolled * 100, 1) if enrolled else None,
        "cohorts": cohorts,
    }


async def load_lms(db: AsyncSession) -> dict:
    global _cache
    if _cache and time.monotonic() - _cache[0] < _TTL:
        return _cache[1]
    since = datetime.now(timezone.utc) - timedelta(days=30)
    row = (await db.execute(text(f"""
        SELECT
          (SELECT count(*) FROM lms_attendance_records) AS records,
          (SELECT count(*) FROM lms_attendance_records WHERE status = 'present') AS present,
          (SELECT count(*) FROM lms_attendance_records r JOIN lms_attendance_sessions s ON s.attendance_session_id = r.attendance_session_id
            WHERE coalesce(s.actual_start_time, s.created_at) >= :since) AS records_30d,
          (SELECT count(*) FROM lms_attendance_records r JOIN lms_attendance_sessions s ON s.attendance_session_id = r.attendance_session_id
            WHERE r.status = 'present' AND coalesce(s.actual_start_time, s.created_at) >= :since) AS present_30d,
          (SELECT count(*) FROM lms_attendance_sessions WHERE coalesce(actual_start_time, created_at) >= :since) AS sessions_30d,
          (SELECT count(*) FROM lms_course_enrollments) AS enrolments,
          (SELECT count(*) FROM lms_course_enrollments WHERE status = 'withdrawn') AS withdrawn,
          (SELECT count(DISTINCT student_user_id) FROM lms_course_enrollments WHERE status = 'enrolled') AS enrolled_students,
          (SELECT count(DISTINCT a.student) FROM ({ACTIVITY_SQL}) a
             JOIN lms_course_enrollments e ON e.student_user_id = a.student AND e.status = 'enrolled' WHERE a.at >= :since) AS active_30d
    """), {"since": since})).mappings().first()
    intakes = (await db.execute(text(
        "SELECT student_user_id, date_trunc('month', min(enrolled_at))::date FROM lms_course_enrollments GROUP BY student_user_id"
    ))).all()
    active = (await db.execute(text(
        f"SELECT DISTINCT a.student, date_trunc('month', a.at)::date FROM ({ACTIVITY_SQL}) a"
    ))).all()
    cohorts = build_cohorts([(int(r[0]), r[1]) for r in intakes], [(int(r[0]), r[1]) for r in active], datetime.now(timezone.utc).date())
    result = summarize({key: int(value or 0) for key, value in dict(row).items()}, cohorts)
    _cache = (time.monotonic(), result)
    return result


def context_line(lms: dict) -> str:
    attendance = f"{lms['attendance']}% attendance over {lms['records_30d']} records in 30 days" if lms["attendance"] is not None else "no attendance records in 30 days"
    dropout = f"{lms['dropout']}% of enrolments withdrawn ({lms['withdrawn']} of {lms['enrolments']})" if lms["dropout"] is not None else "no enrolments"
    return (f"LMS (live): {attendance}; {dropout}; {lms['active_30d']} of {lms['enrolled_students']} enrolled students had learning activity in 30 days. "
            "Dropout counts only enrolments marked withdrawn, not inactivity.")
