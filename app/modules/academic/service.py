import json
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.academic.schemas import (
    AcademicLevelCreate,
    ClassFromTemplateRequest,
    CourseCreate,
    NamedNodeCreate,
    PathwayConfirmRequest,
    ProgrammeEnrolmentCreate,
    StudyOptionUpsert,
    TemplateDraftRequest,
    WorkspaceSyncRequest,
)
from app.modules.auth import service as auth_service
from app.modules.auth.schemas import CurrentUser


RESOURCE_TABLES = {
    "programmes": "academic_programmes",
    "levels": "academic_levels",
    "schools": "academic_schools",
}


def _rows(result) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, default=str)


async def list_nodes(db: AsyncSession, resource: str, active_only: bool = False):
    table = RESOURCE_TABLES[resource]
    where = " WHERE status = 'active'" if active_only else ""
    query = text(f"SELECT * FROM {table}{where} ORDER BY position, name")
    return _rows(await db.execute(query))


async def create_node(db: AsyncSession, resource: str, payload: NamedNodeCreate | AcademicLevelCreate):
    table = RESOURCE_TABLES[resource]
    values = payload.model_dump()
    extra_column = ", rank" if resource == "levels" else ""
    extra_value = ", :rank" if resource == "levels" else ""
    try:
        row = (await db.execute(text(
            f"INSERT INTO {table} (code, name, description, status, position{extra_column}) "
            f"VALUES (:code, :name, :description, :status, :position{extra_value}) RETURNING *"
        ), values)).mappings().one()
        await db.commit()
        return dict(row)
    except Exception as exc:
        await db.rollback()
        if "unique" in str(exc).lower():
            raise ConflictError(f"A {resource[:-1]} with that code or name already exists") from exc
        raise


async def update_node(db: AsyncSession, resource: str, node_id: int, payload):
    table = RESOURCE_TABLES[resource]
    id_column = {"programmes": "programme_id", "levels": "level_id", "schools": "school_id"}[resource]
    values = payload.model_dump() | {"node_id": node_id}
    rank = ", rank = :rank" if resource == "levels" else ""
    row = (await db.execute(text(
        f"UPDATE {table} SET code=:code, name=:name, description=:description, status=:status, "
        f"position=:position{rank}, updated_at=now() WHERE {id_column}=:node_id RETURNING *"
    ), values)).mappings().first()
    if row is None:
        raise NotFoundError(f"{resource[:-1].title()} {node_id} not found")
    await db.commit()
    return dict(row)


async def list_courses(
    db: AsyncSession,
    programme_id: int | None = None,
    level_id: int | None = None,
    school_id: int | None = None,
    active_only: bool = False,
):
    conditions = []
    params: dict[str, Any] = {}
    for column, value in (("c.programme_id", programme_id), ("c.level_id", level_id), ("c.school_id", school_id)):
        if value is not None:
            key = column.split(".")[1]
            conditions.append(f"{column} = :{key}")
            params[key] = value
    if active_only:
        conditions.append("c.status = 'active'")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    result = await db.execute(text(f"""
        SELECT c.*, p.name AS programme_name, l.name AS level_name, s.name AS school_name,
               COALESCE(jsonb_agg(jsonb_build_object(
                   'study_mode', o.study_mode, 'price', o.price, 'duration', o.duration,
                   'is_enabled', o.is_enabled
               ) ORDER BY o.study_mode) FILTER (WHERE o.study_option_id IS NOT NULL), '[]'::jsonb) AS study_options
        FROM academic_courses c
        JOIN academic_programmes p ON p.programme_id = c.programme_id
        LEFT JOIN academic_levels l ON l.level_id = c.level_id
        JOIN academic_schools s ON s.school_id = c.school_id
        LEFT JOIN academic_course_study_options o ON o.course_id = c.course_id
        {where}
        GROUP BY c.course_id, p.name, l.name, s.name
        ORDER BY p.position, COALESCE(l.rank, 999), s.position, c.title
    """), params)
    return _rows(result)


async def upsert_course(db: AsyncSession, payload: CourseCreate, course_id: int | None = None):
    values = payload.model_dump(exclude={"study_options"}) | {"course_id": course_id}
    if payload.level_id is not None:
        valid_level = await db.scalar(text(
            "SELECT 1 FROM academic_programme_levels WHERE programme_id=:programme_id AND level_id=:level_id AND is_active"
        ), values)
        if not valid_level:
            raise ValidationError("The selected level is not enabled for this programme")
    if course_id is None:
        row = (await db.execute(text("""
            INSERT INTO academic_courses
                (programme_id, level_id, school_id, slug, code, title, awarding_body, blurb, image_url, status)
            VALUES
                (:programme_id, :level_id, :school_id, :slug, :code, :title, :awarding_body, :blurb, :image_url, :status)
            RETURNING course_id
        """), values)).mappings().one()
        course_id = row["course_id"]
    else:
        changed = await db.execute(text("""
            UPDATE academic_courses SET programme_id=:programme_id, level_id=:level_id, school_id=:school_id,
                slug=:slug, code=:code, title=:title, awarding_body=:awarding_body, blurb=:blurb,
                image_url=:image_url, status=:status, updated_at=now()
            WHERE course_id=:course_id RETURNING course_id
        """), values)
        if changed.first() is None:
            raise NotFoundError(f"Course {course_id} not found")
    for option in payload.study_options:
        await upsert_study_option(db, course_id, option, commit=False)
    await db.commit()
    courses = await list_courses(db)
    return next(item for item in courses if item["course_id"] == course_id)


async def upsert_study_option(db: AsyncSession, course_id: int, payload: StudyOptionUpsert, commit: bool = True):
    row = (await db.execute(text("""
        INSERT INTO academic_course_study_options (course_id, study_mode, price, duration, is_enabled)
        VALUES (:course_id, :study_mode, :price, :duration, :is_enabled)
        ON CONFLICT (course_id, study_mode) DO UPDATE SET price=EXCLUDED.price,
            duration=EXCLUDED.duration, is_enabled=EXCLUDED.is_enabled, updated_at=now()
        RETURNING *
    """), {"course_id": course_id, **payload.model_dump()})).mappings().one()
    if commit:
        await db.commit()
    return dict(row)


async def set_programme_levels(db: AsyncSession, programme_id: int, level_ids: list[int]):
    await db.execute(text("UPDATE academic_programme_levels SET is_active=false WHERE programme_id=:id"), {"id": programme_id})
    for level_id in set(level_ids):
        await db.execute(text("""
            INSERT INTO academic_programme_levels (programme_id, level_id, is_active)
            VALUES (:programme_id, :level_id, true)
            ON CONFLICT (programme_id, level_id) DO UPDATE SET is_active=true
        """), {"programme_id": programme_id, "level_id": level_id})
    await db.commit()
    return _rows(await db.execute(text("""
        SELECT l.* FROM academic_levels l JOIN academic_programme_levels pl USING (level_id)
        WHERE pl.programme_id=:id AND pl.is_active ORDER BY l.rank NULLS LAST, l.position, l.name
    """), {"id": programme_id}))


async def public_levels(db: AsyncSession, programme_id: int):
    return _rows(await db.execute(text("""
        SELECT l.* FROM academic_levels l JOIN academic_programme_levels pl USING (level_id)
        WHERE pl.programme_id=:id AND pl.is_active AND l.status='active'
        ORDER BY l.rank NULLS LAST, l.position, l.name
    """), {"id": programme_id}))


async def public_schools(db: AsyncSession, programme_id: int, level_id: int | None):
    return _rows(await db.execute(text("""
        SELECT DISTINCT s.* FROM academic_schools s JOIN academic_courses c USING (school_id)
        WHERE c.programme_id=:programme_id AND (:level_id IS NULL OR c.level_id=:level_id)
          AND c.status='active' AND s.status='active'
        ORDER BY s.position, s.name
    """), {"programme_id": programme_id, "level_id": level_id}))


async def create_programme_enrolment(db: AsyncSession, payload: ProgrammeEnrolmentCreate):
    programme_exists = await db.scalar(text("SELECT 1 FROM academic_programmes WHERE programme_id=:id AND status='active'"), {"id": payload.programme_id})
    if not programme_exists:
        raise ValidationError("The selected programme is not available")
    if payload.preferred_level_id is not None:
        valid = await db.scalar(text("SELECT 1 FROM academic_programme_levels WHERE programme_id=:programme_id AND level_id=:level_id AND is_active"), payload.model_dump())
        if not valid:
            raise ValidationError("The preferred level is not available for this programme")
    if payload.preferred_course_id is not None:
        preferred = (await db.execute(text("""
            SELECT programme_id, level_id, school_id FROM academic_courses
            WHERE course_id=:course_id AND status='active'
        """), {"course_id": payload.preferred_course_id})).mappings().first()
        if preferred is None or preferred["programme_id"] != payload.programme_id:
            raise ValidationError("The preferred course is not part of this programme")
        if payload.preferred_level_id is not None and preferred["level_id"] != payload.preferred_level_id:
            raise ValidationError("The preferred course is not part of this academic level")
        if payload.preferred_school_id is not None and preferred["school_id"] != payload.preferred_school_id:
            raise ValidationError("The preferred course is not part of this school")
        if payload.preferred_study_mode is not None:
            enabled = await db.scalar(text("""
                SELECT 1 FROM academic_course_study_options WHERE course_id=:course_id
                  AND study_mode=:study_mode AND is_enabled
            """), {"course_id": payload.preferred_course_id, "study_mode": payload.preferred_study_mode})
            if not enabled:
                raise ValidationError("The preferred study mode is not enabled for this course")
    email = str(payload.email).strip().lower()
    user_id = await db.scalar(text("SELECT user_id FROM users WHERE lower(email)=:email"), {"email": email})
    account_created = user_id is None
    if account_created:
        user_id = await db.scalar(text("""
            INSERT INTO users (email, full_name, is_active) VALUES (:email, :name, true) RETURNING user_id
        """), {"email": email, "name": payload.full_name.strip()})
        student_number = f"STU-{user_id:06d}"
        await db.execute(text("""
            INSERT INTO lms_student_profiles (user_id, student_number, phone)
            VALUES (:user_id, :number, :phone)
        """), {"user_id": user_id, "number": student_number, "phone": payload.phone.strip()})
    else:
        await db.execute(text("""
            INSERT INTO lms_student_profiles (user_id, student_number, phone)
            VALUES (:user_id, :number, :phone) ON CONFLICT (user_id) DO UPDATE SET phone=EXCLUDED.phone
        """), {"user_id": user_id, "number": f"STU-{user_id:06d}", "phone": payload.phone.strip()})
    await db.execute(text("""
        INSERT INTO user_access_levels (user_id, access_level_id)
        SELECT :user_id, access_level_id FROM access_levels WHERE access_key IN ('LMS','STUDENT')
        ON CONFLICT DO NOTHING
    """), {"user_id": user_id})
    duplicate = await db.scalar(text("""
        SELECT enrolment_id FROM academic_programme_enrolments
        WHERE student_user_id=:user_id AND programme_id=:programme_id
          AND status IN ('awaiting_counselling','counselling','pathway_selected')
    """), {"user_id": user_id, "programme_id": payload.programme_id})
    if duplicate:
        await db.rollback()
        raise ConflictError("This student already has an active enrolment for the selected programme")
    enrolment_id = await db.scalar(text("""
        INSERT INTO academic_programme_enrolments
          (student_user_id, programme_id, preferred_level_id, preferred_school_id,
           preferred_course_id, preferred_study_mode, status, source)
        VALUES (:user_id, :programme_id, :preferred_level_id, :preferred_school_id,
                :preferred_course_id, :preferred_study_mode, 'awaiting_counselling', 'main_ui')
        RETURNING enrolment_id
    """), {"user_id": user_id, **payload.model_dump(exclude={"full_name", "email", "phone"})})
    await db.commit()
    invitation_sent = False
    if account_created:
        try:
            invitation = await auth_service.issue_student_password_setup_invitation(db, user_id, None)
            invitation_sent = invitation.email_sent
        except Exception:
            await db.rollback()
    return {
        "enrolment_id": enrolment_id, "user_id": user_id, "status": "awaiting_counselling",
        "account_created": account_created, "invitation_sent": invitation_sent,
        "message": "Your programme enrolment was received. A counsellor will help you select your pathway.",
    }


async def list_programme_enrolments(db: AsyncSession, status: str | None = None):
    result = await db.execute(text("""
        SELECT e.*, u.full_name, u.email, sp.phone, p.name AS programme_name,
               l.name AS preferred_level_name, s.name AS preferred_school_name,
               c.title AS preferred_course_title
        FROM academic_programme_enrolments e
        JOIN users u ON u.user_id=e.student_user_id
        JOIN lms_student_profiles sp ON sp.user_id=e.student_user_id
        JOIN academic_programmes p ON p.programme_id=e.programme_id
        LEFT JOIN academic_levels l ON l.level_id=e.preferred_level_id
        LEFT JOIN academic_schools s ON s.school_id=e.preferred_school_id
        LEFT JOIN academic_courses c ON c.course_id=e.preferred_course_id
        WHERE (:status IS NULL OR e.status=:status)
        ORDER BY e.created_at DESC
    """), {"status": status})
    return _rows(result)


async def list_my_enrolments(db: AsyncSession, user_id: int):
    programmes = _rows(await db.execute(text("""
        SELECT e.enrolment_id, e.status, e.created_at, p.code AS programme_code,
          p.name AS programme_name
        FROM academic_programme_enrolments e
        JOIN academic_programmes p ON p.programme_id=e.programme_id
        WHERE e.student_user_id=:user_id ORDER BY e.created_at DESC
    """), {"user_id": user_id}))
    courses = _rows(await db.execute(text("""
        SELECT e.course_enrolment_id, e.status, e.study_mode, e.agreed_price,
          e.agreed_duration, e.class_id, e.enrolled_at, c.code AS course_code,
          c.title AS course_title, cl.code AS class_code, cl.name AS class_name
        FROM academic_course_enrolments e
        JOIN academic_courses c ON c.course_id=e.course_id
        LEFT JOIN lms_classes cl ON cl.class_id=e.class_id
        WHERE e.student_user_id=:user_id ORDER BY e.enrolled_at DESC
    """), {"user_id": user_id}))
    return {"programme_enrolments": programmes, "course_enrolments": courses}


async def academic_analytics(db: AsyncSession):
    totals = (await db.execute(text("""
        SELECT count(*) AS enquiries,
          count(*) FILTER (WHERE status IN ('awaiting_counselling','counselling')) AS awaiting_counselling,
          count(*) FILTER (WHERE status='pathway_selected') AS converted,
          count(*) FILTER (WHERE created_at >= now() - interval '30 days') AS enquiries_30d
        FROM academic_programme_enrolments
    """))).mappings().one()
    demand = _rows(await db.execute(text("""
        SELECT c.course_id, c.code, c.title, p.name AS programme_name,
          count(DISTINCT e.student_user_id) AS unique_students,
          count(*) FILTER (WHERE e.enrolled_at >= now() - interval '30 days') AS new_enrolments_30d
        FROM academic_courses c JOIN academic_programmes p ON p.programme_id=c.programme_id
        LEFT JOIN academic_course_enrolments e ON e.course_id=c.course_id
        GROUP BY c.course_id, p.name ORDER BY unique_students DESC, c.title LIMIT 10
    """)))
    data = dict(totals)
    data["conversion_percent"] = round((data["converted"] / data["enquiries"] * 100), 1) if data["enquiries"] else 0
    data["course_demand"] = demand
    return data


async def confirm_pathway(db: AsyncSession, enrolment_id: int, payload: PathwayConfirmRequest, counsellor_id: int):
    enrolment = (await db.execute(text("SELECT * FROM academic_programme_enrolments WHERE enrolment_id=:id"), {"id": enrolment_id})).mappings().first()
    if enrolment is None:
        raise NotFoundError(f"Programme enrolment {enrolment_id} not found")
    option = (await db.execute(text("""
        SELECT o.*, c.programme_id FROM academic_course_study_options o
        JOIN academic_courses c ON c.course_id=o.course_id
        WHERE o.course_id=:course_id AND o.study_mode=:study_mode AND o.is_enabled
    """), payload.model_dump())).mappings().first()
    if option is None or option["programme_id"] != enrolment["programme_id"]:
        raise ValidationError("The selected course and study mode do not belong to this programme")
    duplicate = await db.scalar(text("""
        SELECT course_enrolment_id FROM academic_course_enrolments
        WHERE student_user_id=:student_user_id AND course_id=:course_id AND study_mode=:study_mode
          AND status IN ('awaiting_class','active')
    """), {"student_user_id": enrolment["student_user_id"], **payload.model_dump()})
    if duplicate:
        raise ConflictError("This student already has an active enrolment for this course and study mode")
    course_enrolment_id = await db.scalar(text("""
        INSERT INTO academic_course_enrolments
          (programme_enrolment_id, student_user_id, course_id, study_mode, agreed_price,
           agreed_duration, status, confirmed_by)
        VALUES (:programme_enrolment_id, :student_user_id, :course_id, :study_mode,
                :price, :duration, :status, :confirmed_by)
        RETURNING course_enrolment_id
    """), {
        "programme_enrolment_id": enrolment_id, "student_user_id": enrolment["student_user_id"],
        "course_id": payload.course_id, "study_mode": payload.study_mode,
        "price": option["price"], "duration": option["duration"],
        "status": "active" if payload.class_id else "awaiting_class", "confirmed_by": counsellor_id,
    })
    if payload.class_id:
        class_row = (await db.execute(text("""
            SELECT class_id FROM lms_classes WHERE class_id=:class_id AND academic_course_id=:course_id
              AND study_mode=:study_mode
        """), payload.model_dump())).first()
        if class_row is None:
            raise ValidationError("The selected class does not match the course and study mode")
        await db.execute(text("""
            INSERT INTO lms_class_students (class_id, student_user_id, assigned_by)
            VALUES (:class_id, :student_user_id, :assigned_by) ON CONFLICT DO NOTHING
        """), {"class_id": payload.class_id, "student_user_id": enrolment["student_user_id"], "assigned_by": counsellor_id})
        await db.execute(text("UPDATE academic_course_enrolments SET class_id=:class_id WHERE course_enrolment_id=:id"), {"class_id": payload.class_id, "id": course_enrolment_id})
    await db.execute(text("""
        UPDATE academic_programme_enrolments SET status='pathway_selected', counsellor_user_id=:user_id,
          updated_at=now() WHERE enrolment_id=:id
    """), {"user_id": counsellor_id, "id": enrolment_id})
    await db.commit()
    return {"course_enrolment_id": course_enrolment_id, "status": "active" if payload.class_id else "awaiting_class"}


async def list_templates(db: AsyncSession, course_id: int):
    return _rows(await db.execute(text("""
        SELECT t.*, v.version_number, v.template_version_id, v.status AS version_status,
               v.snapshot, v.published_at, d.template_version_id AS draft_version_id,
               d.version_number AS draft_version_number, d.snapshot AS draft_snapshot,
               lc.code AS source_course_code, lc.title AS source_course_title,
               lc.description AS source_course_description, lc.status AS source_course_status
        FROM academic_course_templates t
        JOIN lms_courses lc ON lc.course_id=t.source_lms_course_id
        LEFT JOIN academic_course_template_versions v ON v.template_version_id=t.active_version_id
        LEFT JOIN academic_course_template_versions d ON d.template_id=t.template_id AND d.status='draft'
        WHERE t.course_id=:course_id ORDER BY t.study_mode
    """), {"course_id": course_id}))


async def build_course_snapshot(db: AsyncSession, source_course_id: int):
    sections = _rows(await db.execute(text("""
        SELECT module_id, title, description, vimeo_folder_uri, position, status
        FROM lms_modules WHERE course_id=:id ORDER BY position
    """), {"id": source_course_id}))
    items = _rows(await db.execute(text("""
        SELECT i.* FROM lms_learning_items i JOIN lms_modules m ON m.module_id=i.module_id
        WHERE m.course_id=:id ORDER BY m.position, i.position
    """), {"id": source_course_id}))
    items_by_section: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        item["template_key"] = f"legacy-item-{item['learning_item_id']}"
        items_by_section.setdefault(item["module_id"], []).append(item)
    for section in sections:
        section["template_key"] = f"legacy-section-{section['module_id']}"
        section["items"] = items_by_section.get(section["module_id"], [])
    assessments = _rows(await db.execute(text("""
        SELECT exam_id, assessment_kind, title, instructions, available_from, due_at,
          duration_minutes, randomize_questions, randomize_options, grades_released, status
        FROM lms_exams WHERE course_id=:id ORDER BY created_at
    """), {"id": source_course_id}))
    for assessment in assessments:
        assessment["template_key"] = f"legacy-assessment-{assessment['exam_id']}"
        assessment["questions"] = _rows(await db.execute(text("""
            SELECT question_id, question_type, prompt, marks, position, options,
              correct_option_index, correct_option_indices, accepted_answers
            FROM lms_exam_questions WHERE exam_id=:id ORDER BY position
        """), {"id": assessment["exam_id"]}))
    return {"sections": sections, "assessments": assessments}


async def list_academic_classes(db: AsyncSession):
    return _rows(await db.execute(text("""
        SELECT cl.class_id, cl.code, cl.name, cl.description, cl.start_date, cl.end_date,
          cl.delivery_mode, cl.timezone, cl.capacity, cl.status, cl.academic_course_id AS course_id,
          cl.study_mode, cl.source_template_version_id, cl.last_synced_version_id,
          c.code AS course_code, c.title AS course_title, p.name AS programme_name,
          s.name AS school_name, l.name AS level_name
        FROM lms_classes cl
        JOIN academic_courses c ON c.course_id=cl.academic_course_id
        JOIN academic_programmes p ON p.programme_id=c.programme_id
        JOIN academic_schools s ON s.school_id=c.school_id
        LEFT JOIN academic_levels l ON l.level_id=c.level_id
        ORDER BY cl.start_date DESC, cl.name
    """)))


async def staff_can_manage_course(db: AsyncSession, course_id: int, user: CurrentUser) -> bool:
    if any(role in user.access for role in ("SUPER_ADMIN", "ADMIN")):
        return True
    if "LECTURER" not in user.access:
        return False
    return bool(await db.scalar(text("""
        SELECT 1 FROM academic_courses ac
        JOIN lms_courses lc ON lc.program_id=ac.legacy_program_id
        JOIN lms_course_lecturers lcl ON lcl.course_id=lc.course_id
        WHERE ac.course_id=:course_id AND lcl.lecturer_user_id=:user_id LIMIT 1
    """), {"course_id": course_id, "user_id": user.user_id}))


async def user_can_access_class(db: AsyncSession, class_id: int, user: CurrentUser) -> bool:
    if any(role in user.access for role in ("SUPER_ADMIN", "ADMIN")):
        return True
    if "LECTURER" in user.access:
        table, column = "lms_class_lecturers", "lecturer_user_id"
    elif "STUDENT" in user.access:
        table, column = "lms_class_students", "student_user_id"
    else:
        return False
    return bool(await db.scalar(text(f"SELECT 1 FROM {table} WHERE class_id=:class_id AND {column}=:user_id"), {"class_id": class_id, "user_id": user.user_id}))


async def save_template_draft(db: AsyncSession, payload: TemplateDraftRequest, user_id: int):
    option = await db.scalar(text("""
        SELECT 1 FROM academic_course_study_options WHERE course_id=:course_id
          AND study_mode=:study_mode AND is_enabled
    """), payload.model_dump())
    if not option:
        raise ValidationError("Enable this study mode for the course before creating its template")
    source_id = await db.scalar(text("""
        SELECT source_lms_course_id FROM academic_course_templates
        WHERE course_id=:course_id AND study_mode=:study_mode
    """), payload.model_dump())
    if source_id is None:
        course = (await db.execute(text("SELECT legacy_program_id, code, title FROM academic_courses WHERE course_id=:course_id"), payload.model_dump())).mappings().first()
        if course is None or course["legacy_program_id"] is None:
            raise ValidationError("This course needs a legacy catalogue link before LMS content can be created")
        source_id = await db.scalar(text("""
            INSERT INTO lms_courses (program_id, code, title, description, status, created_by)
            VALUES (:program_id, :code, :title, 'Reusable class template', 'draft', :user_id)
            RETURNING course_id
        """), {"program_id": course["legacy_program_id"], "code": f"{course['code']}-{payload.study_mode[:2].upper()}-T", "title": f"{course['title']} · {payload.study_mode.replace('_',' ').title()}", "user_id": user_id})
        await db.execute(text("""
            INSERT INTO lms_course_lecturers (course_id, lecturer_user_id, assigned_by)
            SELECT :course_id, :user_id, :user_id
            WHERE EXISTS (SELECT 1 FROM lms_lecturer_profiles WHERE user_id=:user_id)
            ON CONFLICT DO NOTHING
        """), {"course_id": source_id, "user_id": user_id})
    template_id = await db.scalar(text("""
        INSERT INTO academic_course_templates (course_id, study_mode, title, source_lms_course_id, created_by)
        VALUES (:course_id, :study_mode, :title, :source_id, :user_id)
        ON CONFLICT (course_id, study_mode) DO UPDATE SET title=EXCLUDED.title, updated_at=now()
        RETURNING template_id
    """), {**payload.model_dump(exclude={"snapshot"}), "source_id": source_id, "user_id": user_id})
    draft_snapshot = payload.snapshot or await build_course_snapshot(db, source_id)
    draft_id = await db.scalar(text("""
        SELECT template_version_id FROM academic_course_template_versions
        WHERE template_id=:template_id AND status='draft'
    """), {"template_id": template_id})
    if draft_id:
        await db.execute(text("UPDATE academic_course_template_versions SET snapshot=CAST(:snapshot AS jsonb), updated_at=now() WHERE template_version_id=:id"), {"snapshot": _json(draft_snapshot), "id": draft_id})
    else:
        next_version = await db.scalar(text("SELECT COALESCE(max(version_number),0)+1 FROM academic_course_template_versions WHERE template_id=:id"), {"id": template_id})
        draft_id = await db.scalar(text("""
            INSERT INTO academic_course_template_versions (template_id, version_number, status, snapshot, created_by)
            VALUES (:template_id, :version, 'draft', CAST(:snapshot AS jsonb), :user_id) RETURNING template_version_id
        """), {"template_id": template_id, "version": next_version, "snapshot": _json(draft_snapshot), "user_id": user_id})
    await db.commit()
    return {"template_id": template_id, "draft_version_id": draft_id}


async def publish_template(db: AsyncSession, template_id: int, snapshot: dict[str, Any] | None, user_id: int):
    version = (await db.execute(text("""
        SELECT * FROM academic_course_template_versions WHERE template_id=:id AND status='draft'
    """), {"id": template_id})).mappings().first()
    if version is None:
        raise ValidationError("Save a draft before publishing this template")
    final_snapshot = snapshot if snapshot is not None else version["snapshot"]
    await db.execute(text("""
        UPDATE academic_course_template_versions SET status='published', snapshot=CAST(:snapshot AS jsonb),
          published_by=:user_id, published_at=now(), updated_at=now() WHERE template_version_id=:version_id
    """), {"snapshot": _json(final_snapshot), "user_id": user_id, "version_id": version["template_version_id"]})
    await db.execute(text("UPDATE academic_course_templates SET active_version_id=:version_id, updated_at=now() WHERE template_id=:id"), {"version_id": version["template_version_id"], "id": template_id})
    await db.commit()
    return {"template_id": template_id, "template_version_id": version["template_version_id"], "version_number": version["version_number"], "status": "published"}


async def create_class_from_template(db: AsyncSession, payload: ClassFromTemplateRequest, user_id: int):
    template = (await db.execute(text("""
        SELECT t.*, v.template_version_id, v.snapshot FROM academic_course_templates t
        JOIN academic_course_template_versions v ON v.template_version_id=t.active_version_id
        WHERE t.template_id=:id AND v.status='published'
    """), {"id": payload.template_id})).mappings().first()
    if template is None:
        raise ValidationError("Select a template with a published version")
    class_id = await db.scalar(text("""
        INSERT INTO lms_classes
          (course_id, code, name, description, start_date, end_date, delivery_mode, timezone,
           capacity, status, created_by, academic_course_id, study_mode, source_template_version_id,
           last_synced_version_id, content_snapshot)
        VALUES (:source_id, :code, :name, :description, :start_date, :end_date, :delivery_mode,
                :timezone, :capacity, 'planned', :user_id, :academic_course_id, :study_mode,
                :version_id, :version_id, CAST(:snapshot AS jsonb))
        RETURNING class_id
    """), {
        **payload.model_dump(exclude={"template_id"}), "source_id": template["source_lms_course_id"],
        "user_id": user_id, "academic_course_id": template["course_id"], "study_mode": template["study_mode"],
        "version_id": template["template_version_id"], "snapshot": _json(template["snapshot"]),
    })
    await db.commit()
    return {"class_id": class_id, "course_id": template["course_id"], "study_mode": template["study_mode"], "source_template_version_id": template["template_version_id"], "snapshot": template["snapshot"]}


async def get_workspace(db: AsyncSession, class_id: int):
    row = (await db.execute(text("""
        SELECT cl.class_id, cl.code, cl.name, cl.academic_course_id AS course_id, cl.study_mode,
          cl.source_template_version_id, cl.last_synced_version_id, cl.content_snapshot,
          c.title AS course_title
        FROM lms_classes cl JOIN academic_courses c ON c.course_id=cl.academic_course_id
        WHERE cl.class_id=:id
    """), {"id": class_id})).mappings().first()
    if row is None:
        raise NotFoundError(f"Class {class_id} not found")
    return dict(row)


async def update_workspace(db: AsyncSession, class_id: int, snapshot: dict[str, Any]):
    row = (await db.execute(text("UPDATE lms_classes SET content_snapshot=CAST(:snapshot AS jsonb), updated_at=now() WHERE class_id=:id RETURNING class_id"), {"snapshot": _json(snapshot), "id": class_id})).first()
    if row is None:
        raise NotFoundError(f"Class {class_id} not found")
    await db.commit()
    return await get_workspace(db, class_id)


def _by_key(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("template_key")): item for item in snapshot.get("sections", []) if item.get("template_key")}


async def compare_workspace(db: AsyncSession, class_id: int, template_version_id: int):
    workspace = await get_workspace(db, class_id)
    template_snapshot = await db.scalar(text("SELECT snapshot FROM academic_course_template_versions WHERE template_version_id=:id AND status='published'"), {"id": template_version_id})
    if template_snapshot is None:
        raise NotFoundError(f"Published template version {template_version_id} not found")
    base_snapshot = await db.scalar(text("SELECT snapshot FROM academic_course_template_versions WHERE template_version_id=:id"), {"id": workspace["last_synced_version_id"]}) if workspace["last_synced_version_id"] else {}
    current, proposed, base = _by_key(workspace["content_snapshot"] or {}), _by_key(template_snapshot or {}), _by_key(base_snapshot or {})
    protected_statuses = {"active", "published", "released", "completed"}
    conflicting_keys = {
        key for key in current.keys() & proposed.keys()
        if current[key] != proposed[key]
        and (current[key] != base.get(key) or current[key].get("status") in protected_statuses or current[key].get("completed_count", 0) > 0)
    }
    return {
        "added": [item for key, item in proposed.items() if key not in current],
        "updated": [item for key, item in proposed.items() if key in current and item != current[key] and key not in conflicting_keys],
        "conflicting": [{"current": current[key], "template": proposed[key]} for key in sorted(conflicting_keys)],
        "removed": [item for key, item in current.items() if key not in proposed],
        "protected_removals": [item for key, item in current.items() if key not in proposed],
    }


async def sync_workspace(db: AsyncSession, class_id: int, payload: WorkspaceSyncRequest):
    workspace = await get_workspace(db, class_id)
    template_snapshot = await db.scalar(text("SELECT snapshot FROM academic_course_template_versions WHERE template_version_id=:id AND status='published'"), {"id": payload.template_version_id})
    if template_snapshot is None:
        raise NotFoundError(f"Published template version {payload.template_version_id} not found")
    current = _by_key(workspace["content_snapshot"] or {})
    proposed = _by_key(template_snapshot or {})
    for key in payload.template_keys:
        if key in proposed:
            existing = current.get(key)
            if existing and (existing.get("status") in {"active", "published", "released", "completed"} or existing.get("completed_count", 0) > 0):
                continue
            current[key] = proposed[key]
    merged = dict(workspace["content_snapshot"] or {})
    merged["sections"] = list(current.values())
    await db.execute(text("""
        UPDATE lms_classes SET content_snapshot=CAST(:snapshot AS jsonb), last_synced_version_id=:version_id,
          updated_at=now() WHERE class_id=:class_id
    """), {"snapshot": _json(merged), "version_id": payload.template_version_id, "class_id": class_id})
    await db.commit()
    return await get_workspace(db, class_id)
