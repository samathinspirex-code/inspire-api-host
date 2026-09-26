import asyncio
import base64
import binascii
import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError, ConflictError, NotFoundError, ValidationError
from app.core.public_form_email import form_email_html, send_public_form_email
from app.modules.academic.schemas import (
    AcademicLevelCreate,
    AdmissionApplicationCreate,
    ContactInquiryCreate,
    ClassDetailsUpdate,
    ClassFromCourseRequest,
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
from app.core.config import settings
from app.modules.cms import media_service


logger = logging.getLogger(__name__)


RESOURCE_TABLES = {
    "programmes": "academic_programmes",
    "levels": "academic_levels",
    "schools": "academic_schools",
}


def _rows(result) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


async def _store_admission_document(payload, submission_id: str) -> str:
    if not settings.MEDIA_BUCKET:
        raise ValidationError("Result document storage is not configured. Continue without a document or contact admissions.")
    try:
        content = base64.b64decode(payload.data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("The result document could not be read") from exc
    if len(content) != payload.size_bytes or len(content) > 10_485_760:
        raise ValidationError("The result document size is invalid")
    extension = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}[payload.content_type]
    safe_stem = re.sub(r"[^a-z0-9]+", "-", Path(payload.filename).stem.lower()).strip("-")[:70] or "results"
    key = f"admissions/results/{submission_id}/{uuid4().hex}-{safe_stem}{extension}"
    try:
        await asyncio.to_thread(
            media_service._client().put_object,
            Bucket=settings.MEDIA_BUCKET,
            Key=key,
            Body=content,
            ContentType=payload.content_type,
            ServerSideEncryption="AES256",
        )
    except Exception as exc:
        raise ValidationError("The result document could not be stored securely. Continue without it or try again.") from exc
    return key


async def create_admission_lead(db: AsyncSession, payload: AdmissionApplicationCreate):
    submission_id = str(payload.submission_id)
    existing = (await db.execute(text("SELECT lead_id, status FROM crm_admission_leads WHERE submission_id=:submission_id"), {"submission_id": submission_id})).mappings().first()
    if existing:
        return {"lead_id": existing["lead_id"], "status": existing["status"], "message": "Your application is already on our admissions list."}
    if not await db.scalar(text("SELECT 1 FROM academic_programmes WHERE programme_id=:id AND status='active'"), {"id": payload.programme_id}):
        raise ValidationError("The selected programme is not available")
    if payload.preferred_course_id is not None:
        valid_course = await db.scalar(text("""
            SELECT 1 FROM academic_courses
            WHERE course_id=:course_id AND programme_id=:programme_id AND status='active'
        """), {"course_id": payload.preferred_course_id, "programme_id": payload.programme_id})
        if not valid_course:
            raise ValidationError("The selected course is not part of this programme")
    document_key = None
    if payload.result_document:
        document_key = await _store_admission_document(payload.result_document, submission_id)
    document = payload.result_document
    try:
        lead_id = await db.scalar(text("""
            INSERT INTO crm_admission_leads
              (submission_id, full_name, email, phone, highest_qualification, programme_id,
               preferred_course_id, result_document_key, result_document_name,
               result_document_content_type, status, source)
            VALUES (:submission_id, :full_name, :email, :phone, :highest_qualification, :programme_id,
                    :preferred_course_id, :document_key, :document_name, :document_content_type,
                    'new_inquiry', 'website_admissions')
            RETURNING lead_id
        """), {
            "submission_id": submission_id,
            "full_name": payload.full_name.strip(),
            "email": str(payload.email).strip().lower(),
            "phone": payload.phone.strip(),
            "highest_qualification": payload.highest_qualification.strip(),
            "programme_id": payload.programme_id,
            "preferred_course_id": payload.preferred_course_id,
            "document_key": document_key,
            "document_name": document.filename if document else None,
            "document_content_type": document.content_type if document else None,
        })
        await db.commit()
    except Exception:
        await db.rollback()
        if document_key:
            try:
                await asyncio.to_thread(media_service._client().delete_object, Bucket=settings.MEDIA_BUCKET, Key=document_key)
            except Exception:
                pass
        raise
    pathway = (await db.execute(text("""
        SELECT p.name AS programme_name, c.title AS course_name, c.awarding_body,
               o.price, o.duration
        FROM academic_programmes p
        LEFT JOIN academic_courses c ON c.course_id=:course_id
        LEFT JOIN academic_course_study_options o
          ON o.course_id=c.course_id AND o.study_mode=:study_mode AND o.is_enabled=true
        WHERE p.programme_id=:programme_id
    """), {"programme_id": payload.programme_id, "course_id": payload.preferred_course_id, "study_mode": payload.preferred_study_mode})).mappings().first()
    submitted_at = datetime.now(timezone.utc).strftime("%d %B %Y at %H:%M UTC")
    study_mode = payload.preferred_study_mode.replace("_", " ").title() if payload.preferred_study_mode else "Not selected"
    price = f"Rs {int(pathway['price']):,}" if pathway and pathway["price"] is not None else "To be confirmed"

    # Mirror into modern CRM leads pipeline
    try:
        new_crm_lead = (await db.execute(text("""
            INSERT INTO crm_leads
              (full_name, email, phone, highest_qualification, interested_programme, interested_course, source, stage, priority, notes, is_archived)
            VALUES
              (:full_name, :email, :phone, :highest_qualification, :interested_programme, :interested_course, 'website_admission', 'new_inquiry', 'high', :notes, FALSE)
            RETURNING lead_id
        """), {
            "full_name": payload.full_name.strip(),
            "email": str(payload.email).strip().lower(),
            "phone": payload.phone.strip(),
            "highest_qualification": payload.highest_qualification.strip(),
            "interested_programme": pathway["programme_name"] if pathway else None,
            "interested_course": pathway["course_name"] if pathway else None,
            "notes": f"Submitted via Website Admission Form. Submission ID: {submission_id}",
        })).mappings().one()
        await db.execute(text("""
            INSERT INTO crm_activities (lead_id, activity_type, content)
            VALUES (:lead_id, 'note', :content)
        """), {
            "lead_id": new_crm_lead["lead_id"],
            "content": f"New application submitted via website for {(pathway['course_name'] if pathway else None) or 'Academic Pathway'}",
        })
        await db.commit()
    except Exception as exc:
        logger.warning("Failed to mirror admission lead into crm_leads: %s", exc)
    rows = [
        ("Lead ID", str(lead_id)),
        ("Submitted", submitted_at),
        ("Applicant", payload.full_name.strip()),
        ("Email", str(payload.email)),
        ("Phone", payload.phone.strip()),
        ("Highest qualification", payload.highest_qualification.strip()),
        ("Awarding body", pathway["awarding_body"] if pathway else None),
        ("Programme", pathway["programme_name"] if pathway else None),
        ("Course", pathway["course_name"] if pathway else None),
        ("Study option", study_mode),
        ("Duration", pathway["duration"] if pathway else None),
        ("Course fee", price),
        ("Result document", document.filename if document else "Not uploaded — applicant will provide later"),
    ]
    text_body = "New admission application\n\n" + "\n".join(f"{label}: {value or '—'}" for label, value in rows)
    email_result = await send_public_form_email(
        subject=f"New admission application — {(pathway['course_name'] if pathway else None) or payload.full_name.strip()}",
        text_body=text_body,
        html_body=form_email_html("New admission application", "A prospective student submitted the admissions form.", rows),
        reply_to=str(payload.email),
        custom_id=f"admission-{submission_id}",
    )
    if not email_result.sent:
        logger.warning("Admission lead %s was saved but its notification email failed: %s", lead_id, email_result.error)
    return {"lead_id": lead_id, "status": "new_inquiry", "message": "Your application was received. An advisor will contact you within one business day."}


async def send_contact_inquiry(db: AsyncSession, payload: ContactInquiryCreate):

    # Mirror into modern CRM leads pipeline
    try:
        lead_row = (await db.execute(text("""
            INSERT INTO crm_leads
              (full_name, email, phone, message, source, stage, priority, notes, is_archived)
            VALUES
              (:full_name, :email, :phone, :message, 'website_contact', 'new_inquiry', 'medium', :notes, FALSE)
            RETURNING lead_id
        """), {
            "full_name": payload.full_name.strip(),
            "email": str(payload.email).strip().lower(),
            "phone": "Not provided",
            "message": payload.message.strip(),
            "notes": f"Website Contact Message:\n{payload.message.strip()}",
        })).mappings().one()
        await db.execute(text("""
            INSERT INTO crm_activities (lead_id, activity_type, content)
            VALUES (:lead_id, 'note', :content)
        """), {
            "lead_id": lead_row["lead_id"],
            "content": f"New contact enquiry submitted on website: {payload.message.strip()}",
        })
        await db.commit()
    except Exception as exc:
        logger.warning("Failed to insert contact lead into crm_leads: %s", exc)
    submitted_at = datetime.now(timezone.utc).strftime("%d %B %Y at %H:%M UTC")
    rows = [
        ("Submitted", submitted_at),
        ("Name", payload.full_name.strip()),
        ("Email", str(payload.email)),
    ]
    text_body = "New website contact enquiry\n\n" + "\n".join(f"{label}: {value}" for label, value in rows) + f"\n\nMessage:\n{payload.message.strip()}"
    result = await send_public_form_email(
        subject=f"New website enquiry — {payload.full_name.strip()}",
        text_body=text_body,
        html_body=form_email_html("New website enquiry", "A visitor submitted the contact form.", rows, payload.message.strip()),
        reply_to=str(payload.email),
        custom_id=f"contact-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
    )
    if not result.sent:
        logger.warning("Contact form notification failed: %s", result.error)
        raise APIError(503, "EMAIL_DELIVERY_FAILED", "Your message could not be sent right now. Please try again shortly.")
    return {"sent": True, "message": "Your message was sent successfully."}


async def list_admission_leads(db: AsyncSession, status: str | None = None):
    rows = _rows(await db.execute(text("""
        SELECT l.lead_id, l.full_name, l.email, l.phone, l.highest_qualification,
               l.status, l.source, l.result_document_name, l.created_at,
               p.name AS programme_name, c.title AS preferred_course_title
        FROM crm_admission_leads l
        JOIN academic_programmes p ON p.programme_id=l.programme_id
        LEFT JOIN academic_courses c ON c.course_id=l.preferred_course_id
        WHERE (:status IS NULL OR l.status=:status)
        ORDER BY l.created_at DESC
    """), {"status": status}))
    return rows


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, default=str)


async def _replace_public_course_content(
    db: AsyncSession, legacy_program_id: int, topics: list[str], outcomes: list[str]
) -> None:
    """Keep the public course detail tabs in sync with the CMS course editor."""
    clean_topics = list(dict.fromkeys(item.strip() for item in topics if item.strip()))
    clean_outcomes = list(dict.fromkeys(item.strip() for item in outcomes if item.strip()))
    await db.execute(text("DELETE FROM topics WHERE program_id=:program_id"), {"program_id": legacy_program_id})
    await db.execute(text("DELETE FROM outcomes WHERE program_id=:program_id"), {"program_id": legacy_program_id})
    for order, topic in enumerate(clean_topics, start=1):
        await db.execute(text("INSERT INTO topics (program_id, \"order\", topic) VALUES (:program_id, :order, :topic)"), {
            "program_id": legacy_program_id, "order": order, "topic": topic,
        })
    for order, outcome in enumerate(clean_outcomes, start=1):
        await db.execute(text("INSERT INTO outcomes (program_id, \"order\", outcome) VALUES (:program_id, :order, :outcome)"), {
            "program_id": legacy_program_id, "order": order, "outcome": outcome,
        })


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


async def delete_node(db: AsyncSession, resource: str, node_id: int):
    table = RESOURCE_TABLES[resource]
    id_column = {"programmes": "programme_id", "levels": "level_id", "schools": "school_id"}[resource]
    exists = await db.scalar(text(f"SELECT 1 FROM {table} WHERE {id_column}=:id"), {"id": node_id})
    if not exists:
        raise NotFoundError(f"{resource[:-1].title()} {node_id} not found")

    reference_checks = {
        "programmes": "SELECT EXISTS(SELECT 1 FROM academic_courses WHERE programme_id=:id) OR EXISTS(SELECT 1 FROM academic_programme_enrolments WHERE programme_id=:id)",
        "levels": "SELECT EXISTS(SELECT 1 FROM academic_courses WHERE level_id=:id) OR EXISTS(SELECT 1 FROM academic_programme_enrolments WHERE preferred_level_id=:id)",
        "schools": "SELECT EXISTS(SELECT 1 FROM academic_courses WHERE school_id=:id) OR EXISTS(SELECT 1 FROM academic_programme_enrolments WHERE preferred_school_id=:id)",
    }
    referenced = bool(await db.scalar(text(reference_checks[resource]), {"id": node_id}))
    if referenced:
        await db.execute(text(f"UPDATE {table} SET status='archived', updated_at=now() WHERE {id_column}=:id"), {"id": node_id})
        disposition = "archived"
    else:
        if resource == "programmes":
            await db.execute(text("DELETE FROM academic_programme_levels WHERE programme_id=:id"), {"id": node_id})
        elif resource == "levels":
            await db.execute(text("DELETE FROM academic_programme_levels WHERE level_id=:id"), {"id": node_id})
        await db.execute(text(f"DELETE FROM {table} WHERE {id_column}=:id"), {"id": node_id})
        disposition = "deleted"
    await db.commit()
    return {"id": node_id, "disposition": disposition}


async def delete_course(db: AsyncSession, course_id: int):
    course = (await db.execute(text("SELECT legacy_program_id, school_id, programme_id FROM academic_courses WHERE course_id=:id"), {"id": course_id})).mappings().first()
    if course is None:
        raise NotFoundError(f"Course {course_id} not found")
    referenced = bool(await db.scalar(text("""
        SELECT EXISTS(SELECT 1 FROM academic_course_enrolments WHERE course_id=:id)
          OR EXISTS(SELECT 1 FROM academic_course_templates WHERE course_id=:id)
          OR EXISTS(SELECT 1 FROM lms_classes WHERE academic_course_id=:id)
    """), {"id": course_id}))
    if referenced:
        await db.execute(text("UPDATE academic_courses SET status='archived', updated_at=now() WHERE course_id=:id"), {"id": course_id})
        disposition = "archived"
    else:
        legacy_id = course["legacy_program_id"]
        await db.execute(text("DELETE FROM academic_courses WHERE course_id=:id"), {"id": course_id})
        if legacy_id is not None:
            legacy_referenced = await db.scalar(text("SELECT 1 FROM lms_courses WHERE program_id=:id LIMIT 1"), {"id": legacy_id})
            if not legacy_referenced:
                await db.execute(text("DELETE FROM programs WHERE program_id=:id"), {"id": legacy_id})
        disposition = "deleted"
    await db.commit()
    return {"id": course_id, "disposition": disposition}


async def archive_master_course_workspace(db: AsyncSession, course_id: int, user: CurrentUser):
    """Remove a reusable Course page from active use without destroying cohorts."""
    await ensure_staff_can_manage_lms_course(db, course_id, user)
    course = (await db.execute(text("SELECT course_id, is_class_copy FROM lms_courses WHERE course_id=:id"), {"id": course_id})).mappings().first()
    if course is None:
        raise NotFoundError(f"Course {course_id} not found")
    if course["is_class_copy"]:
        raise ValidationError("Remove a copied class from My Classes, not from Courses")
    await db.execute(text("UPDATE lms_courses SET status='archived', updated_at=now() WHERE course_id=:id"), {"id": course_id})
    await db.commit()
    return {"course_id": course_id, "disposition": "archived"}


async def archive_class_workspace(db: AsyncSession, class_id: int, user: CurrentUser):
    """Hide a class and its copied Course workspace while preserving its history."""
    class_row = (await db.execute(text("SELECT class_id, course_id FROM lms_classes WHERE class_id=:id"), {"id": class_id})).mappings().first()
    if class_row is None:
        raise NotFoundError(f"Class {class_id} not found")
    if not await user_can_access_class(db, class_id, user):
        raise ValidationError("You are not assigned to this class")
    await db.execute(text("UPDATE lms_classes SET status='cancelled', updated_at=now() WHERE class_id=:id"), {"id": class_id})
    await db.execute(text("UPDATE lms_courses SET status='archived', updated_at=now() WHERE course_id=:course_id"), {"course_id": class_row["course_id"]})
    await db.commit()
    return {"class_id": class_id, "disposition": "archived"}


async def update_class_workspace_status(db: AsyncSession, class_id: int, status: str, user: CurrentUser):
    """Move an intake from planned to active without changing its content."""
    class_row = (await db.execute(text("SELECT class_id, status FROM lms_classes WHERE class_id=:id"), {"id": class_id})).mappings().first()
    if class_row is None:
        raise NotFoundError(f"Class {class_id} not found")
    if not await user_can_access_class(db, class_id, user):
        raise ValidationError("You are not assigned to this class")
    if class_row["status"] == "cancelled":
        raise ValidationError("A removed class cannot be reactivated")
    await db.execute(text("UPDATE lms_classes SET status=:status, updated_at=now() WHERE class_id=:id"), {"id": class_id, "status": status})
    await db.commit()
    return {"class_id": class_id, "status": status}


async def update_class_workspace_details(db: AsyncSession, class_id: int, payload: ClassDetailsUpdate, user: CurrentUser):
    """Update an intake without changing its copied course, enrolments, or tracking history."""
    class_row = (await db.execute(text("""
        SELECT cl.class_id, cl.status, lc.is_orientation
        FROM lms_classes cl
        JOIN lms_courses lc ON lc.course_id=cl.course_id
        WHERE cl.class_id=:id
    """), {"id": class_id})).mappings().first()
    if class_row is None:
        raise NotFoundError(f"Class {class_id} not found")
    if not await user_can_access_class(db, class_id, user):
        raise ValidationError("You are not assigned to this class")
    if class_row["status"] == "cancelled":
        raise ValidationError("A removed class cannot be edited")
    if not class_row["is_orientation"] and payload.study_mode is None:
        raise ValidationError("Select a study mode")
    code = payload.code.strip().upper()
    duplicate = await db.scalar(text("""
        SELECT 1 FROM lms_classes
        WHERE class_id <> :class_id AND lower(code)=lower(:code)
    """), {"class_id": class_id, "code": code})
    if duplicate:
        raise ConflictError(f"Class code '{code}' is already in use")
    await db.execute(text("""
        UPDATE lms_classes
        SET code=:code, name=:name, description=:description,
            start_date=:start_date, end_date=:end_date,
            delivery_mode=:delivery_mode, study_mode=:study_mode,
            timezone=:timezone, capacity=:capacity, status=:status, updated_at=now()
        WHERE class_id=:class_id
    """), {
        **payload.model_dump(), "class_id": class_id, "code": code,
        "name": payload.name.strip(),
        "study_mode": None if class_row["is_orientation"] else payload.study_mode,
        "description": payload.description.strip() if payload.description else None,
    })
    await db.commit()
    classes = await list_academic_classes(db)
    return next(item for item in classes if item["class_id"] == class_id)


async def list_courses(
    db: AsyncSession,
    programme_id: int | None = None,
    school_id: int | None = None,
    active_only: bool = False,
):
    conditions = []
    params: dict[str, Any] = {}
    for column, value in (("c.programme_id", programme_id), ("c.school_id", school_id)):
        if value is not None:
            key = column.split(".")[1]
            conditions.append(f"{column} = :{key}")
            params[key] = value
    if active_only:
        conditions.append("c.status = 'active'")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    result = await db.execute(text(f"""
        SELECT c.*, p.name AS programme_name, s.name AS school_name,
               COALESCE((SELECT popularity FROM programs lp WHERE lp.program_id=c.legacy_program_id), 0) AS popularity,
               COALESCE((SELECT jsonb_agg(jsonb_build_object('topic_id', t.topic_id, 'order', t.\"order\", 'topic', t.topic) ORDER BY t.\"order\")
                         FROM topics t WHERE t.program_id=c.legacy_program_id), '[]'::jsonb) AS topics,
               COALESCE((SELECT jsonb_agg(jsonb_build_object('outcome_id', o2.outcome_id, 'order', o2.\"order\", 'outcome', o2.outcome) ORDER BY o2.\"order\")
                         FROM outcomes o2 WHERE o2.program_id=c.legacy_program_id), '[]'::jsonb) AS outcomes,
               COALESCE(jsonb_agg(jsonb_build_object(
                   'study_mode', o.study_mode, 'price', o.price, 'duration', o.duration,
                   'is_enabled', o.is_enabled
               ) ORDER BY o.study_mode) FILTER (WHERE o.study_option_id IS NOT NULL), '[]'::jsonb) AS study_options
        FROM academic_courses c
        JOIN academic_programmes p ON p.programme_id = c.programme_id
        JOIN academic_schools s ON s.school_id = c.school_id
        LEFT JOIN academic_course_study_options o ON o.course_id = c.course_id
        {where}
         GROUP BY c.course_id, p.name, p.position, s.name, s.position
         ORDER BY s.position, p.position, c.title
    """), params)
    return _rows(result)


async def upsert_course(db: AsyncSession, payload: CourseCreate, course_id: int | None = None):
    values = payload.model_dump(exclude={"study_options", "topics", "outcomes", "popularity"}) | {"course_id": course_id}
    values["slug"] = payload.slug.strip()
    duplicate = (await db.execute(text("""
        SELECT title FROM academic_courses
        WHERE lower(slug)=lower(:slug) AND course_id IS DISTINCT FROM :course_id
        UNION ALL
        SELECT p.title FROM programs p
        LEFT JOIN academic_courses c ON c.legacy_program_id=p.program_id
        WHERE lower(p.slug)=lower(:slug) AND c.course_id IS DISTINCT FROM :course_id
        LIMIT 1
    """), values)).mappings().first()
    if duplicate:
        raise ConflictError(
            f"The public URL slug '{values['slug']}' is already used by {duplicate['title']}. "
            "Choose a different slug, such as one containing the school or programme name."
        )
    if course_id is None:
        row = (await db.execute(text("""
            INSERT INTO academic_courses
                (programme_id, level_id, school_id, slug, code, title, awarding_body, entry_requirements, progression_route, blurb, image_url, status)
            VALUES
                (:programme_id, NULL, :school_id, :slug, :code, :title, :awarding_body, :entry_requirements, :progression_route, :blurb, :image_url, :status)
            RETURNING course_id
        """), values)).mappings().one()
        course_id = row["course_id"]
    else:
        changed = await db.execute(text("""
            UPDATE academic_courses SET programme_id=:programme_id, level_id=NULL, school_id=:school_id,
                slug=:slug, code=:code, title=:title, awarding_body=:awarding_body,
                entry_requirements=:entry_requirements, progression_route=:progression_route, blurb=:blurb,
                image_url=:image_url, status=:status, updated_at=now()
            WHERE course_id=:course_id RETURNING course_id
        """), values)
        if changed.first() is None:
            raise NotFoundError(f"Course {course_id} not found")
    for option in payload.study_options:
        await upsert_study_option(db, course_id, option, commit=False)
    course = (await db.execute(text("""
        SELECT c.*, p.name AS programme_name, s.name AS school_name,
          COALESCE((SELECT min(price) FROM academic_course_study_options WHERE course_id=c.course_id AND is_enabled), 0) AS price_from,
          COALESCE((SELECT duration FROM academic_course_study_options WHERE course_id=c.course_id AND is_enabled ORDER BY study_option_id LIMIT 1), 'To be confirmed') AS duration
        FROM academic_courses c JOIN academic_programmes p ON p.programme_id=c.programme_id
        JOIN academic_schools s ON s.school_id=c.school_id WHERE c.course_id=:id
    """), {"id": course_id})).mappings().one()
    legacy_values = {
        "slug": course["slug"], "title": course["title"], "level": course["programme_name"][:100],
        "school": course["school_name"][:100], "awarding_body": course["awarding_body"],
        "code": course["code"], "duration": course["duration"], "price_from": course["price_from"],
        "image_url": course["image_url"], "blurb": course["blurb"], "image_label": course["title"][:100],
        "popularity": payload.popularity,
    }
    if course["legacy_program_id"] is None:
        legacy_id = await db.scalar(text("""
            INSERT INTO programs (slug,title,level,school,awarding_body,code,duration,price_from,tag,icon,image_label,image_url,blurb,popularity)
            VALUES (:slug,:title,:level,:school,:awarding_body,:code,:duration,:price_from,NULL,'grad',:image_label,:image_url,:blurb,:popularity)
            RETURNING program_id
        """), legacy_values)
        await db.execute(text("UPDATE academic_courses SET legacy_program_id=:legacy_id WHERE course_id=:course_id"), {"legacy_id": legacy_id, "course_id": course_id})
    else:
        await db.execute(text("""
            UPDATE programs SET slug=:slug,title=:title,level=:level,school=:school,
              awarding_body=:awarding_body,code=:code,duration=:duration,price_from=:price_from,
              image_label=:image_label,image_url=:image_url,blurb=:blurb,popularity=:popularity
            WHERE program_id=:legacy_id
        """), {**legacy_values, "legacy_id": course["legacy_program_id"]})
    legacy_program_id = course["legacy_program_id"]
    if legacy_program_id is None:
        legacy_program_id = await db.scalar(text("SELECT legacy_program_id FROM academic_courses WHERE course_id=:course_id"), {"course_id": course_id})
    await _replace_public_course_content(db, legacy_program_id, payload.topics, payload.outcomes)
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
    await db.execute(text("""
        UPDATE academic_course_study_options
        SET price=:price, duration=:duration, updated_at=now()
        WHERE course_id=:course_id AND study_option_id<>:study_option_id
    """), {
        "course_id": course_id,
        "study_option_id": row["study_option_id"],
        "price": payload.price,
        "duration": payload.duration,
    })
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


async def public_programmes(db: AsyncSession, school_id: int | None = None):
    course_filter = "" if school_id is None else """
          AND EXISTS (
            SELECT 1 FROM academic_courses c
            WHERE c.programme_id=p.programme_id AND c.status='active'
              AND c.school_id=:school_id
          )
    """
    return _rows(await db.execute(text(f"""
        SELECT p.*
        FROM academic_programmes p
        WHERE p.status='active'
          {course_filter}
        ORDER BY p.position, p.name
    """), {"school_id": school_id}))


async def public_levels(db: AsyncSession, programme_id: int, school_id: int | None = None):
    school_filter = "AND c.school_id=:school_id AND c.status='active'" if school_id is not None else ""
    return _rows(await db.execute(text(f"""
        SELECT DISTINCT l.*
        FROM academic_levels l
        JOIN academic_courses c ON c.level_id=l.level_id
        WHERE c.programme_id=:programme_id AND c.status='active' AND l.status='active'
          {school_filter}
        ORDER BY l.rank NULLS LAST, l.position, l.name
    """), {"programme_id": programme_id, "school_id": school_id}))


async def public_schools(db: AsyncSession):
    return await list_nodes(db, "schools", True)


async def create_programme_enrolment(db: AsyncSession, payload: ProgrammeEnrolmentCreate):
    programme_exists = await db.scalar(text("SELECT 1 FROM academic_programmes WHERE programme_id=:id AND status='active'"), {"id": payload.programme_id})
    if not programme_exists:
        raise ValidationError("The selected programme is not available")
    if payload.preferred_school_id is not None:
        valid_school_programme = await db.scalar(text("""
            SELECT 1 FROM academic_courses
            WHERE school_id=:preferred_school_id AND programme_id=:programme_id AND status='active'
            LIMIT 1
        """), payload.model_dump())
        if not valid_school_programme:
            raise ValidationError("The selected programme is not available in this school")
    if payload.preferred_course_id is not None:
        preferred = (await db.execute(text("""
            SELECT programme_id, school_id FROM academic_courses
            WHERE course_id=:course_id AND status='active'
        """), {"course_id": payload.preferred_course_id})).mappings().first()
        if preferred is None or preferred["programme_id"] != payload.programme_id:
            raise ValidationError("The preferred course is not part of this programme")
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
          AND preferred_school_id IS NOT DISTINCT FROM :preferred_school_id
          AND status IN ('awaiting_counselling','counselling','pathway_selected')
    """), {"user_id": user_id, "programme_id": payload.programme_id, "preferred_school_id": payload.preferred_school_id})
    if duplicate:
        await db.rollback()
        raise ConflictError("This student already has an active enrolment for the selected programme")
    enrolment_id = await db.scalar(text("""
        INSERT INTO academic_programme_enrolments
          (student_user_id, programme_id, preferred_level_id, preferred_school_id,
           preferred_course_id, preferred_study_mode, status, source)
        VALUES (:user_id, :programme_id, NULL, :preferred_school_id,
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
               s.name AS preferred_school_name,
               c.title AS preferred_course_title
        FROM academic_programme_enrolments e
        JOIN users u ON u.user_id=e.student_user_id
        JOIN lms_student_profiles sp ON sp.user_id=e.student_user_id
        JOIN academic_programmes p ON p.programme_id=e.programme_id
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
        SELECT o.*, c.programme_id, c.school_id FROM academic_course_study_options o
        JOIN academic_courses c ON c.course_id=o.course_id
        WHERE o.course_id=:course_id AND o.study_mode=:study_mode AND o.is_enabled
    """), payload.model_dump())).mappings().first()
    if option is None or option["programme_id"] != enrolment["programme_id"]:
        raise ValidationError("The selected course and study mode do not belong to this programme")
    if enrolment["preferred_school_id"] is not None and option["school_id"] != enrolment["preferred_school_id"]:
        raise ValidationError("The selected course does not belong to the enrolled school")
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
            SELECT class_id, course_id FROM lms_classes WHERE class_id=:class_id AND academic_course_id=:course_id
              AND study_mode=:study_mode
        """), payload.model_dump())).first()
        if class_row is None:
            raise ValidationError("The selected class does not match the course and study mode")
        await db.execute(text("""
            INSERT INTO lms_class_students (class_id, student_user_id, assigned_by)
            VALUES (:class_id, :student_user_id, :assigned_by) ON CONFLICT DO NOTHING
        """), {"class_id": payload.class_id, "student_user_id": enrolment["student_user_id"], "assigned_by": counsellor_id})
        await db.execute(text("""
            INSERT INTO lms_course_enrollments (course_id, student_user_id, status, enrolled_by)
            VALUES (:course_id, :student_user_id, 'enrolled', :assigned_by)
            ON CONFLICT (course_id, student_user_id) DO UPDATE SET status='enrolled', updated_at=now()
        """), {"course_id": class_row.course_id, "student_user_id": enrolment["student_user_id"], "assigned_by": counsellor_id})
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
          cl.delivery_mode, cl.timezone, cl.capacity, cl.status, cl.academic_course_id AS academic_course_id,
          cl.course_id AS course_id, lc.source_master_course_id AS source_course_id,
          cl.study_mode, cl.source_template_version_id, cl.last_synced_version_id,
          lc.code AS course_code, lc.title AS course_title, lc.is_orientation AS is_orientation,
          c.code AS catalogue_course_code, c.title AS catalogue_course_title,
          c.awarding_body, p.name AS programme_name,
          s.name AS school_name,
          (SELECT count(*) FROM lms_class_students cs WHERE cs.class_id=cl.class_id) AS student_count
        FROM lms_classes cl
        JOIN lms_courses lc ON lc.course_id=cl.course_id
        LEFT JOIN academic_courses c ON c.course_id=cl.academic_course_id
        LEFT JOIN academic_programmes p ON p.programme_id=c.programme_id
        LEFT JOIN academic_schools s ON s.school_id=c.school_id
        WHERE cl.status <> 'cancelled'
        ORDER BY cl.start_date DESC, cl.name
    """)))


async def list_lms_course_study_options(db: AsyncSession, source_course_id: int):
    """Return catalogue destinations available to a reusable LMS Course page."""
    course = (await db.execute(text("""
        SELECT lc.course_id, lc.title AS template_course_title, lc.is_orientation AS is_orientation,
               ac.course_id AS academic_course_id, ac.title AS academic_course_title,
               ac.awarding_body, ac.programme_id, p.name AS programme_name,
               ac.school_id, s.name AS school_name
        FROM lms_courses lc
        LEFT JOIN academic_courses ac ON ac.legacy_program_id=lc.program_id
        LEFT JOIN academic_programmes p ON p.programme_id=ac.programme_id
        LEFT JOIN academic_schools s ON s.school_id=ac.school_id
        WHERE lc.course_id=:course_id AND COALESCE(lc.is_class_copy, FALSE)=FALSE
        ORDER BY ac.course_id
        LIMIT 1
    """), {"course_id": source_course_id})).mappings().first()
    if course is None:
        raise NotFoundError("The selected reusable Course page was not found")
    if course["is_orientation"]:
        return {
            "course_id": source_course_id,
            "template_course_title": course["template_course_title"],
            "is_orientation": True,
            "academic_course_id": None,
            "academic_course_title": None,
            "awarding_body": None,
            "school_id": None,
            "school_name": None,
            "programme_id": None,
            "programme_name": None,
            "study_options": [],
            "academic_courses": [],
        }
    if course["academic_course_id"] is None:
        raise ValidationError("Link this Course page to a CMS academic course before creating a class")
    rows = _rows(await db.execute(text("""
        SELECT ac.course_id, ac.code, ac.title,
               so.study_option_id, so.study_mode, so.price, so.duration, so.is_enabled
        FROM academic_courses ac
        LEFT JOIN academic_course_study_options so ON so.course_id=ac.course_id
        WHERE ac.programme_id=:programme_id AND ac.status <> 'archived'
        ORDER BY ac.title, CASE so.study_mode WHEN 'full_time' THEN 1 ELSE 2 END
    """), {"programme_id": course["programme_id"]}))
    destinations: dict[int, dict] = {}
    for row in rows:
        destination = destinations.setdefault(row["course_id"], {
            "course_id": row["course_id"], "code": row["code"], "title": row["title"],
            "study_options": [],
        })
        if row["study_option_id"] is not None:
            destination["study_options"].append({
                "study_option_id": row["study_option_id"], "course_id": row["course_id"],
                "study_mode": row["study_mode"], "price": row["price"],
                "duration": row["duration"], "is_enabled": row["is_enabled"],
            })
    options = destinations.get(course["academic_course_id"], {}).get("study_options", [])
    return {
        "course_id": source_course_id,
        "template_course_title": course["template_course_title"],
        "is_orientation": False,
        "academic_course_id": course["academic_course_id"],
        "academic_course_title": course["academic_course_title"],
        "awarding_body": course["awarding_body"],
        "school_id": course["school_id"],
        "school_name": course["school_name"],
        "programme_id": course["programme_id"],
        "programme_name": course["programme_name"],
        "study_options": options,
        "academic_courses": list(destinations.values()),
    }


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


async def ensure_staff_can_manage_lms_course(db: AsyncSession, course_id: int, user: CurrentUser) -> None:
    course = (await db.execute(text("""
        SELECT course_id, is_class_copy FROM lms_courses WHERE course_id=:course_id
    """), {"course_id": course_id})).mappings().first()
    if course is None:
        raise NotFoundError(f"Course {course_id} not found")
    if course["is_class_copy"]:
        raise ValidationError("Select a master Course page, not an existing class copy")
    if any(role in user.access for role in ("SUPER_ADMIN", "ADMIN")):
        return
    assigned = await db.scalar(text("""
        SELECT 1 FROM lms_course_lecturers
        WHERE course_id=:course_id AND lecturer_user_id=:user_id
    """), {"course_id": course_id, "user_id": user.user_id})
    if not assigned:
        raise ValidationError("You must be assigned to the Course page before creating its class")


async def create_class_from_course(db: AsyncSession, payload: ClassFromCourseRequest, user_id: int):
    """Create a batch with a fully independent visual Course Studio copy."""
    master = (await db.execute(text("""
        SELECT lc.*, ac.course_id AS academic_course_id
        FROM lms_courses lc
        LEFT JOIN academic_courses ac ON ac.legacy_program_id=lc.program_id
        WHERE lc.course_id=:course_id AND COALESCE(lc.is_class_copy, FALSE)=FALSE
        ORDER BY ac.course_id LIMIT 1
    """), {"course_id": payload.source_course_id})).mappings().first()
    if master is None:
        raise NotFoundError("The selected master Course page was not found")
    orientation = bool(master["is_orientation"])
    if orientation:
        target_course_id = None
        study_mode = None
    else:
        if master["academic_course_id"] is None:
            raise ValidationError("Link this Course page to the academic catalogue before creating a class")
        if payload.academic_course_id is None:
            # A reusable template may intentionally create a programme-level
            # Common class with no exact catalogue course or study-mode link.
            target_course_id = None
            study_mode = None
        else:
            if payload.study_mode is None:
                raise ValidationError("Select a study mode")
            target_course_id = payload.academic_course_id
            study_mode = payload.study_mode
            valid_target = await db.scalar(text("""
                SELECT 1
                FROM academic_courses target
                JOIN academic_courses anchor ON anchor.course_id=:anchor_course_id
                WHERE target.course_id=:target_course_id
                  AND target.programme_id=anchor.programme_id
                  AND target.status <> 'archived'
            """), {"anchor_course_id": master["academic_course_id"], "target_course_id": target_course_id})
            if not valid_target:
                raise ValidationError("Choose a catalogue course from the reusable template's programme")
            enabled = await db.scalar(text("""
                SELECT 1 FROM academic_course_study_options
                WHERE course_id=:course_id AND study_mode=:study_mode AND is_enabled
            """), {"course_id": target_course_id, "study_mode": study_mode})
            if not enabled:
                raise ValidationError("This study mode is not enabled for the selected course")
    duplicate = await db.scalar(text("SELECT 1 FROM lms_classes WHERE lower(code)=lower(:code)"), {"code": payload.code.strip()})
    if duplicate:
        raise ConflictError(f"Class code '{payload.code.strip().upper()}' is already in use")

    copy_code = f"{master['code']}-{payload.code.strip()}".upper()[:100]
    if await db.scalar(text("SELECT 1 FROM lms_courses WHERE lower(code)=lower(:code)"), {"code": copy_code}):
        raise ConflictError("A class workspace with this code already exists")
    copied_course_id = await db.scalar(text("""
        INSERT INTO lms_courses
          (program_id, code, title, description, takeaways, cover_image_url, status,
           created_by, is_class_copy, source_master_course_id, catalogue_course_id, is_orientation)
        VALUES (:program_id, :code, :title, :description, :takeaways, :cover, :status,
                :user_id, TRUE, :source_id, :catalogue_course_id, :is_orientation)
        RETURNING course_id
    """), {
        "program_id": master["program_id"], "code": copy_code, "title": master["title"],
        "description": master["description"], "takeaways": master.get("takeaways"),
        "cover": master.get("cover_image_url"), "status": master["status"],
        "user_id": user_id, "source_id": payload.source_course_id, "catalogue_course_id": target_course_id,
        "is_orientation": orientation,
    })
    class_id = await db.scalar(text("""
        INSERT INTO lms_classes
          (course_id, code, name, description, start_date, end_date, delivery_mode, timezone,
           capacity, status, created_by, academic_course_id, study_mode, content_snapshot)
        VALUES (:course_id, :code, :name, :description, :start_date, :end_date, :delivery_mode,
                :timezone, :capacity, :status, :user_id, :academic_course_id, :study_mode,
                '{"sections":[]}'::jsonb)
        RETURNING class_id
    """), {**payload.model_dump(exclude={"source_course_id", "academic_course_id", "study_mode"}), "course_id": copied_course_id,
             "academic_course_id": target_course_id, "study_mode": study_mode, "user_id": user_id,
             "code": payload.code.strip().upper(), "name": payload.name.strip()})

    module_map: dict[int, int] = {}
    item_map: dict[int, int] = {}
    modules = _rows(await db.execute(text("SELECT * FROM lms_modules WHERE course_id=:id ORDER BY position"), {"id": payload.source_course_id}))
    for module in modules:
        new_module_id = await db.scalar(text("""
            INSERT INTO lms_modules (course_id,title,description,position,status,created_by)
            VALUES (:course_id,:title,:description,:position,:status,:user_id) RETURNING module_id
        """), {**module, "course_id": copied_course_id, "user_id": user_id})
        module_map[module["module_id"]] = new_module_id
        # A class starts with an isolated, locked content workspace.  Copying a
        # master Course page must never expose a section because it happened to
        # be released for a different intake.  The class lecturer releases each
        # copied section explicitly to this class (or an individual student).
        items = _rows(await db.execute(text("SELECT * FROM lms_learning_items WHERE module_id=:id ORDER BY position"), {"id": module["module_id"]}))
        for item in items:
            new_item_id = await db.scalar(text("""
                INSERT INTO lms_learning_items
                  (module_id,item_type,title,description,resource_url,thumbnail_url,text_content,
                   duration_minutes,position,status,is_required,created_by)
                VALUES (:module_id,:item_type,:title,:description,:resource_url,:thumbnail_url,:text_content,
                        :duration_minutes,:position,:status,:is_required,:user_id)
                RETURNING learning_item_id
            """), {**item, "module_id": new_module_id, "user_id": user_id})
            item_map[item["learning_item_id"]] = new_item_id

    await db.execute(text("""
        INSERT INTO lms_course_assistant_settings
          (course_id,is_enabled,assistant_name,welcome_message,fallback_message,attention_animation,created_by)
        SELECT :new_course,is_enabled,assistant_name,welcome_message,fallback_message,attention_animation,:user_id
        FROM lms_course_assistant_settings WHERE course_id=:source_course
        ON CONFLICT (course_id) DO NOTHING
    """), {"new_course": copied_course_id, "source_course": payload.source_course_id, "user_id": user_id})
    for old_item, new_item in item_map.items():
        await db.execute(text("""
            INSERT INTO lms_lecture_questions
              (course_id,learning_item_id,question,option_a,option_b,option_c,option_d,correct_option,
               explanation,difficulty,topic,source_locator,status,generated_by_ai,created_by)
            SELECT :new_course,:new_item,question,option_a,option_b,option_c,option_d,correct_option,
                   explanation,difficulty,topic,source_locator,status,generated_by_ai,:user_id
            FROM lms_lecture_questions WHERE course_id=:source_course AND learning_item_id=:old_item
        """), {"new_course": copied_course_id, "new_item": new_item, "source_course": payload.source_course_id,
                 "old_item": old_item, "user_id": user_id})

    assignment_map: dict[int, int] = {}
    assignments = _rows(await db.execute(text("SELECT * FROM lms_coursework_assignments WHERE course_id=:id ORDER BY assignment_id"), {"id": payload.source_course_id}))
    for assignment in assignments:
        new_assignment_id = await db.scalar(text("""
            INSERT INTO lms_coursework_assignments
              (learning_item_id,course_id,target_type,target_id,title,instructions,assignment_type,
               submission_type,question_paper_asset_id,available_from,due_at,duration_minutes,max_marks,
               allow_late,grades_released,status,created_by)
            VALUES (:learning_item_id,:course_id,'class',:class_id,:title,:instructions,:assignment_type,
                    :submission_type,:question_paper_asset_id,:available_from,:due_at,:duration_minutes,
                    :max_marks,:allow_late,:grades_released,:status,:user_id)
            RETURNING assignment_id
        """), {**assignment, "learning_item_id": item_map.get(assignment.get("learning_item_id")),
                 "course_id": copied_course_id, "class_id": class_id, "user_id": user_id})
        assignment_map[assignment["assignment_id"]] = new_assignment_id
    exams = _rows(await db.execute(text("SELECT * FROM lms_exams WHERE course_id=:id ORDER BY exam_id"), {"id": payload.source_course_id}))
    for exam in exams:
        new_exam_id = await db.scalar(text("""
            INSERT INTO lms_exams
              (assessment_kind,assignment_id,course_id,target_type,target_id,title,instructions,
               available_from,due_at,duration_minutes,randomize_questions,randomize_options,
               grades_released,status,created_by)
            VALUES (:assessment_kind,:assignment_id,:course_id,'class',:class_id,:title,:instructions,
                    :available_from,:due_at,:duration_minutes,:randomize_questions,:randomize_options,
                    :grades_released,:status,:user_id) RETURNING exam_id
        """), {**exam, "assignment_id": assignment_map[exam["assignment_id"]],
                 "course_id": copied_course_id, "class_id": class_id, "user_id": user_id})
        await db.execute(text("""
            INSERT INTO lms_exam_questions
              (exam_id,question_type,prompt,marks,position,options,correct_option_index,
               correct_option_indices,accepted_answers)
            SELECT :new_exam,question_type,prompt,marks,position,options,correct_option_index,
                   correct_option_indices,accepted_answers
            FROM lms_exam_questions WHERE exam_id=:old_exam
        """), {"new_exam": new_exam_id, "old_exam": exam["exam_id"]})

    await db.execute(text("""
        INSERT INTO lms_course_lecturers (course_id,lecturer_user_id,assigned_by)
        SELECT :new_course,lecturer_user_id,:user_id FROM lms_course_lecturers WHERE course_id=:source_course
        ON CONFLICT DO NOTHING
    """), {"new_course": copied_course_id, "source_course": payload.source_course_id, "user_id": user_id})
    await db.execute(text("""
        INSERT INTO lms_class_lecturers (class_id,lecturer_user_id,assigned_by)
        SELECT :class_id,lecturer_user_id,:user_id FROM lms_course_lecturers WHERE course_id=:new_course
        ON CONFLICT DO NOTHING
    """), {"class_id": class_id, "new_course": copied_course_id, "user_id": user_id})
    await db.execute(text("""
        INSERT INTO lms_course_lecturers (course_id,lecturer_user_id,assigned_by)
        SELECT :new_course,:user_id,:user_id WHERE EXISTS
          (SELECT 1 FROM lms_lecturer_profiles WHERE user_id=:user_id)
        ON CONFLICT DO NOTHING
    """), {"new_course": copied_course_id, "user_id": user_id})
    await db.execute(text("""
        INSERT INTO lms_class_lecturers (class_id,lecturer_user_id,assigned_by)
        SELECT :class_id,:user_id,:user_id WHERE EXISTS
          (SELECT 1 FROM lms_lecturer_profiles WHERE user_id=:user_id)
        ON CONFLICT DO NOTHING
    """), {"class_id": class_id, "user_id": user_id})
    await db.commit()
    return {"class_id": class_id, "course_id": copied_course_id,
            "source_course_id": payload.source_course_id, "study_mode": study_mode}


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
            INSERT INTO lms_courses (program_id, catalogue_course_id, code, title, description, status, created_by)
            VALUES (:program_id, :course_id, :code, :title, 'Reusable class template', 'draft', :user_id)
            RETURNING course_id
        """), {"program_id": course["legacy_program_id"], "course_id": payload.course_id, "code": f"{course['code']}-{payload.study_mode[:2].upper()}-T", "title": f"{course['title']} · {payload.study_mode.replace('_',' ').title()}", "user_id": user_id})
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
    # Keep legacy template-based creation consistent with the current visual
    # Course-page flow: a lecturer who creates an intake immediately appears in
    # both its template access list and its independent class teaching team.
    await db.execute(text("""
        INSERT INTO lms_course_lecturers (course_id, lecturer_user_id, assigned_by)
        SELECT :course_id, :user_id, :user_id
        WHERE EXISTS (SELECT 1 FROM lms_lecturer_profiles WHERE user_id=:user_id)
        ON CONFLICT DO NOTHING
    """), {"course_id": template["source_lms_course_id"], "user_id": user_id})
    await db.execute(text("""
        INSERT INTO lms_class_lecturers (class_id, lecturer_user_id, assigned_by)
        SELECT :class_id, :user_id, :user_id
        WHERE EXISTS (SELECT 1 FROM lms_lecturer_profiles WHERE user_id=:user_id)
        ON CONFLICT DO NOTHING
    """), {"class_id": class_id, "user_id": user_id})
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
