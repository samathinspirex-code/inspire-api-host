import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.cms import media_service
from app.modules.cms.models import MediaAsset
from app.modules.auth.models import AccessLevel, User, UserAccessLevel
from app.modules.lms.models import (
    ClassLecturer, ClassStudent, CourseEnrollment, CourseLecturer, LmsAnnouncement, LmsClass,
    LmsCourse, LmsCourseworkAssignment, LmsCourseworkSubmission, LmsExam, LmsNotification, OnlineMeeting, StudentProfile,
)
from app.modules.lms.notification_email import send_notification_email
from app.modules.lms.schemas import AnnouncementItem, AnnouncementListResponse, NotificationDispatchSummary, NotificationItem, NotificationListResponse
from app.modules.lms.schemas.notification import AnnouncementAttachment

COLOMBO = timezone(timedelta(hours=5, minutes=30), "Asia/Colombo")
logger = logging.getLogger(__name__)
ASSIGNMENT_REMINDERS = (1440, 60)
# 1 day, 30 minutes, 15 minutes before, and at start time.
MEETING_REMINDERS = (1440, 30, 15, 0)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_time(value: datetime, time_zone: str = "Asia/Colombo") -> str:
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(time_zone)
    except ZoneInfoNotFoundError:
        zone = COLOMBO
        time_zone = "Asia/Colombo"
    return f"{current.astimezone(zone):%d %B %Y at %I:%M %p} ({time_zone})"


def reminder_due(event_time: datetime, offset_minutes: int, now: datetime, grace_minutes: int = 10) -> bool:
    scheduled = event_time - timedelta(minutes=offset_minutes)
    return scheduled <= now <= scheduled + timedelta(minutes=grace_minutes)


async def _audience_user_ids(db: AsyncSession, audience_type: str, audience_id: int | None) -> list[int]:
    if audience_type == "all":
        result = await db.execute(select(User.user_id).join(UserAccessLevel, UserAccessLevel.user_id == User.user_id).join(AccessLevel, AccessLevel.access_level_id == UserAccessLevel.access_level_id).where(User.is_active.is_(True), AccessLevel.access_key == "LMS").distinct())
        return list(result.scalars().all())
    if audience_type in {"admin", "super_admin"}:
        role_key = "ADMIN" if audience_type == "admin" else "SUPER_ADMIN"
        def has_access(key: str):
            return select(UserAccessLevel.user_id).join(
                AccessLevel, AccessLevel.access_level_id == UserAccessLevel.access_level_id
            ).where(
                UserAccessLevel.user_id == User.user_id,
                AccessLevel.access_key == key,
                AccessLevel.is_active.is_(True),
            ).exists()
        filters = [User.is_active.is_(True), has_access("LMS"), has_access(role_key)]
        if audience_type == "admin":
            filters.append(~has_access("SUPER_ADMIN"))
        result = await db.execute(select(User.user_id).where(*filters))
        return list(result.scalars().all())
    if audience_type == "course":
        students = (await db.execute(select(CourseEnrollment.student_user_id).where(CourseEnrollment.course_id == audience_id, CourseEnrollment.status == "enrolled"))).scalars().all()
        lecturers = (await db.execute(select(CourseLecturer.lecturer_user_id).where(CourseLecturer.course_id == audience_id))).scalars().all()
        return list(set(students) | set(lecturers))
    if audience_type == "class":
        students = (await db.execute(select(ClassStudent.student_user_id).where(ClassStudent.class_id == audience_id))).scalars().all()
        lecturers = (await db.execute(select(ClassLecturer.lecturer_user_id).where(ClassLecturer.class_id == audience_id))).scalars().all()
        class_course = await db.scalar(select(LmsClass.course_id).where(LmsClass.class_id == audience_id))
        course_lecturers = (await db.execute(select(CourseLecturer.lecturer_user_id).where(CourseLecturer.course_id == class_course))).scalars().all() if class_course else []
        return list(set(students) | set(lecturers) | set(course_lecturers))
    return []


async def _student_audience_user_ids(db: AsyncSession, audience_type: str, audience_id: int) -> list[int]:
    if audience_type == "course":
        result = await db.execute(select(CourseEnrollment.student_user_id).where(CourseEnrollment.course_id == audience_id, CourseEnrollment.status == "enrolled"))
    else:
        result = await db.execute(select(ClassStudent.student_user_id).where(ClassStudent.class_id == audience_id))
    return list(result.scalars().all())


async def _assignment_lecturer_ids(db: AsyncSession, assignment: LmsCourseworkAssignment) -> list[int]:
    if assignment.target_type == "class":
        rows = await db.execute(select(ClassLecturer.lecturer_user_id).join(User, User.user_id == ClassLecturer.lecturer_user_id).where(ClassLecturer.class_id == assignment.target_id, User.is_active.is_(True)))
        lecturers = set(rows.scalars().all())
        if lecturers:
            return list(lecturers)
        rows = await db.execute(select(CourseLecturer.lecturer_user_id).join(User, User.user_id == CourseLecturer.lecturer_user_id).where(CourseLecturer.course_id == assignment.course_id, User.is_active.is_(True)))
        return list(set(rows.scalars().all()))
    course_ids = (await db.execute(select(CourseLecturer.lecturer_user_id).join(User, User.user_id == CourseLecturer.lecturer_user_id).where(CourseLecturer.course_id == assignment.course_id, User.is_active.is_(True)))).scalars().all()
    class_ids = (await db.execute(
        select(ClassLecturer.lecturer_user_id).join(LmsClass, LmsClass.class_id == ClassLecturer.class_id)
        .join(User, User.user_id == ClassLecturer.lecturer_user_id)
        .where(LmsClass.course_id == assignment.course_id, LmsClass.status != "cancelled", User.is_active.is_(True))
    )).scalars().all()
    return list(set(course_ids) | set(class_ids))


def _assignment_title(prefix: str, title: str) -> str:
    return f"{prefix}: {title}"[:255]


async def _audience_label(db: AsyncSession, audience_type: str, audience_id: int | None) -> str:
    if audience_type == "all": return "Everyone in the LMS"
    if audience_type == "admin": return "LMS Admins"
    if audience_type == "super_admin": return "LMS Super Admins"
    if audience_type == "course":
        item = await db.get(LmsCourse, audience_id); return f"{item.code} · {item.title}" if item else "Course"
    item = await db.get(LmsClass, audience_id); return f"{item.code} · {item.name}" if item else "Class"


async def _can_manage_audience(db: AsyncSession, role: str, user_id: int, audience_type: str, audience_id: int | None) -> None:
    if role in {"SUPER_ADMIN", "ADMIN"}: return
    if role != "LECTURER" or audience_type == "all": raise ForbiddenError("Only administrators can announce to the entire LMS")
    if audience_type == "course" and await db.get(CourseLecturer, (audience_id, user_id)) is not None: return
    if audience_type == "class":
        class_ = await db.get(LmsClass, audience_id)
        if class_ and (await db.get(ClassLecturer, (audience_id, user_id)) is not None or await db.get(CourseLecturer, (class_.course_id, user_id)) is not None): return
    raise ForbiddenError("You can announce only to courses or classes assigned to you")


async def _enqueue(db: AsyncSession, user_ids: list[int], event_key: str, notification_type: str, title: str, message: str, action_url: str | None, importance: str, scheduled_for: datetime, email_enabled: bool, event_at: datetime | None = None, update_existing: bool = False) -> int:
    if not user_ids: return 0
    zones = dict((await db.execute(select(StudentProfile.user_id, StudentProfile.time_zone).where(StudentProfile.user_id.in_(user_ids)))).all()) if event_at else {}
    default_time = local_time(event_at) if event_at else ""
    values = [{"user_id": user_id, "event_key": event_key, "notification_type": notification_type,
        "title": title, "message": message.replace(default_time, local_time(event_at, zones.get(user_id) or "Asia/Colombo")) if event_at else message,
        "action_url": action_url, "importance": importance,
        "scheduled_for": scheduled_for, "email_enabled": email_enabled,
        "email_status": "pending" if email_enabled else "disabled"} for user_id in user_ids]
    statement = insert(LmsNotification).values(values)
    if update_existing:
        statement = statement.on_conflict_do_update(
            index_elements=["user_id", "event_key"],
            set_={
                "scheduled_for": statement.excluded.scheduled_for,
                "title": statement.excluded.title,
                "message": statement.excluded.message,
                "action_url": statement.excluded.action_url,
            },
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=["user_id", "event_key"])
    result = await db.execute(statement.returning(LmsNotification.notification_id))
    return len(result.scalars().all())


async def _announcement_item(db: AsyncSession, item: LmsAnnouncement) -> AnnouncementItem:
    ids = item.attachment_asset_ids or []
    assets = {asset.media_asset_id: asset for asset in (await db.execute(
        select(MediaAsset).where(MediaAsset.media_asset_id.in_(ids), MediaAsset.status == "ready")
    )).scalars().all()} if ids else {}
    return AnnouncementItem(announcement_id=item.announcement_id, audience_type=item.audience_type,
        audience_id=item.audience_id, audience_label=await _audience_label(db, item.audience_type, item.audience_id),
        title=item.title, message=item.message, importance=item.importance, publish_at=item.publish_at,
        attachments=[AnnouncementAttachment(media_asset_id=asset.media_asset_id,
            name=asset.original_filename, content_type=asset.content_type,
            size_bytes=asset.size_bytes, url=media_service._public_url(asset.object_key))
            for asset_id in ids if (asset := assets.get(asset_id))],
        expires_at=item.expires_at, status=item.status, email_enabled=item.email_enabled,
        created_by=item.created_by, created_at=item.created_at)


async def create_announcement(db: AsyncSession, payload, user_id: int, role: str) -> AnnouncementItem:
    await _can_manage_audience(db, role, user_id, payload.audience_type, payload.audience_id)
    if payload.attachment_asset_ids:
        assets = (await db.execute(select(MediaAsset).where(MediaAsset.media_asset_id.in_(payload.attachment_asset_ids)))).scalars().all()
        if len(assets) != len(payload.attachment_asset_ids) or any(
            asset.created_by != user_id or asset.folder != "announcement-attachments" or asset.status != "ready"
            for asset in assets
        ):
            raise ValidationError("Choose completed announcement uploads from your account")
    now = utc_now(); status = payload.status
    if status == "published" and payload.publish_at > now: status = "scheduled"
    item = LmsAnnouncement(**payload.model_dump(exclude={"status"}), status=status, created_by=user_id)
    db.add(item); await db.commit(); await db.refresh(item)
    if item.status == "published" or (item.status == "scheduled" and item.publish_at <= now):
        await materialize_announcement(db, item, now)
    return await _announcement_item(db, item)


async def list_announcements(db: AsyncSession, user_id: int, role: str) -> AnnouncementListResponse:
    stmt = select(LmsAnnouncement)
    if role == "LECTURER":
        # Lecturers see announcements they created OR announcements targeting any
        # course or class they are assigned to teach (for shared-course visibility).
        taught_course_ids = list((await db.execute(
            select(CourseLecturer.course_id).where(CourseLecturer.lecturer_user_id == user_id)
        )).scalars().all())
        taught_class_ids = list((await db.execute(
            select(ClassLecturer.class_id).where(ClassLecturer.lecturer_user_id == user_id)
        )).scalars().all())
        lecturer_filters = [LmsAnnouncement.created_by == user_id]
        if taught_course_ids:
            lecturer_filters.append(
                and_(LmsAnnouncement.audience_type == "course",
                     LmsAnnouncement.audience_id.in_(taught_course_ids))
            )
        if taught_class_ids:
            lecturer_filters.append(
                and_(LmsAnnouncement.audience_type == "class",
                     LmsAnnouncement.audience_id.in_(taught_class_ids))
            )
        stmt = stmt.where(or_(*lecturer_filters))
    elif role not in {"SUPER_ADMIN", "ADMIN"}:
        raise ForbiddenError("Use Notifications to view published announcements")
    rows = list((await db.execute(stmt.order_by(LmsAnnouncement.publish_at.desc()))).scalars().all())
    return AnnouncementListResponse(data=[await _announcement_item(db, item) for item in rows])


async def get_announcement(db: AsyncSession, announcement_id: int, user_id: int, role: str) -> AnnouncementItem:
    item = await db.get(LmsAnnouncement, announcement_id)
    if item is None:
        raise NotFoundError("Announcement not found")
    if role not in {"SUPER_ADMIN", "ADMIN"} and item.created_by != user_id:
        visible = item.status == "published" and item.publish_at <= utc_now() and (
            item.expires_at is None or item.expires_at > utc_now()
        )
        recipient = await db.scalar(select(LmsNotification.notification_id).where(
            LmsNotification.user_id == user_id,
            LmsNotification.event_key == f"announcement:{announcement_id}",
        )) if visible else None
        if recipient is None:
            raise ForbiddenError("This announcement is not available to your account")
    return await _announcement_item(db, item)


async def complete_announcement_upload(db: AsyncSession, asset_id: int, user_id: int):
    asset = await db.get(MediaAsset, asset_id)
    if asset is None:
        raise NotFoundError("Media asset not found")
    if asset.created_by != user_id or asset.folder != "announcement-attachments":
        raise ForbiddenError("This upload does not belong to your account")
    return await media_service.complete_upload(db, asset_id)


async def update_announcement_status(db: AsyncSession, announcement_id: int, status: str, user_id: int, role: str) -> AnnouncementItem:
    item = await db.get(LmsAnnouncement, announcement_id)
    if item is None: raise NotFoundError("Announcement not found")
    if role not in {"SUPER_ADMIN", "ADMIN"} and item.created_by != user_id: raise ForbiddenError("You cannot change this announcement")
    item.status = status
    if status == "published": item.publish_at = utc_now()
    await db.commit()
    if status == "published": await materialize_announcement(db, item, utc_now())
    return await _announcement_item(db, item)


async def materialize_announcement(db: AsyncSession, item: LmsAnnouncement, now: datetime) -> int:
    if item.status in {"cancelled", "expired", "draft"}: return 0
    users = await _audience_user_ids(db, item.audience_type, item.audience_id)
    created = await _enqueue(db, users, f"announcement:{item.announcement_id}", "announcement", item.title,
        item.message, f"{settings.LMS_UI_URL.rstrip('/')}?view=announcements&announcement_id={item.announcement_id}", item.importance,
        item.publish_at, item.email_enabled)
    item.status = "published"; await db.commit()
    return created


async def notify_meeting_change(db: AsyncSession, meeting: OnlineMeeting, class_: LmsClass, course: LmsCourse, event: str, class_ids: list[int] | None = None) -> int:
    users = set()
    for class_id in class_ids or [meeting.class_id]:
        users.update(await _audience_user_ids(db, "class", class_id))
    users.add(meeting.lecturer_user_id)
    labels = {"created": "Online class scheduled", "updated": "Online class rescheduled", "cancelled": "Online class cancelled"}
    title = f"{labels[event]}: {meeting.title}"
    audience = getattr(meeting, "audience_label", None) or f"{course.code} · {class_.name}"
    message = f"{audience} — {local_time(meeting.start_time)}."
    if event == "cancelled": message += " This class will no longer take place at the scheduled time."
    else: message += " Open the LMS for the latest class details."
    created = await _enqueue(db, list(users), f"meeting:{meeting.meeting_id}:{event}:{meeting.updated_at.isoformat() if meeting.updated_at else meeting.start_time.isoformat()}",
        "class_schedule", title, message, f"{settings.LMS_UI_URL.rstrip('/')}?view=meetings",
        "urgent" if event == "cancelled" else "important", utc_now(), True, event_at=meeting.start_time)
    await db.commit(); return created


async def notify_grade_release(db: AsyncSession, assignment: LmsCourseworkAssignment) -> int:
    users = await _student_audience_user_ids(db, assignment.target_type, assignment.target_id)
    exam = await db.scalar(select(LmsExam).where(LmsExam.assignment_id == assignment.assignment_id))
    kind = "exam" if exam else "assignment"
    created = await _enqueue(db, users, f"grade:{kind}:{assignment.assignment_id}:released",
        "grade_released", f"New {kind} result: {assignment.title}",
        f"Your marked result for {assignment.title} is now available in Grades.",
        f"{settings.LMS_UI_URL.rstrip('/')}?view=grades", "important", utc_now(), True)
    await db.commit(); return created


async def notify_assessment_published(db: AsyncSession, assignment: LmsCourseworkAssignment, kind: str) -> int:
    users = await _student_audience_user_ids(db, assignment.target_type, assignment.target_id)
    deadline = f" It is due on {local_time(assignment.due_at)}." if assignment.due_at else ""
    now = utc_now()
    available_from = assignment.available_from
    if available_from is not None and available_from.tzinfo is None:
        available_from = available_from.replace(tzinfo=timezone.utc)
    release_at = max(now, available_from) if available_from else now
    created = await _enqueue(db, users, f"{kind}:{assignment.assignment_id}:published", f"{kind}_published",
        f"New {kind}: {assignment.title}", f"A new {kind} has been published for your course.{deadline}",
        f"{settings.LMS_UI_URL.rstrip('/')}?view={'exams' if kind == 'exam' else 'assignments'}",
        "important", release_at, True, event_at=assignment.due_at, update_existing=True)
    await db.commit(); return created


async def list_notifications(db: AsyncSession, user_id: int) -> NotificationListResponse:
    now = utc_now()
    rows = list((await db.execute(select(LmsNotification).where(LmsNotification.user_id == user_id, LmsNotification.scheduled_for <= now).order_by(LmsNotification.created_at.desc()).limit(200))).scalars().all())
    return NotificationListResponse(unread_count=sum(item.read_at is None for item in rows), data=[NotificationItem(
        notification_id=item.notification_id,
        announcement_id=int(item.event_key.split(":", 1)[1]) if item.notification_type == "announcement" and item.event_key.startswith("announcement:") and item.event_key.split(":", 1)[1].isdigit() else None,
        notification_type=item.notification_type, title=item.title,
        message=item.message, action_url=item.action_url, importance=item.importance,
        scheduled_for=item.scheduled_for, read_at=item.read_at, email_status=item.email_status,
        created_at=item.created_at) for item in rows])


async def set_notification_read(db: AsyncSession, notification_id: int, read: bool, user_id: int) -> NotificationListResponse:
    item = await db.get(LmsNotification, notification_id)
    if item is None or item.user_id != user_id: raise NotFoundError("Notification not found")
    item.read_at = utc_now() if read else None; await db.commit()
    return await list_notifications(db, user_id)


async def mark_all_read(db: AsyncSession, user_id: int) -> NotificationListResponse:
    rows = (await db.execute(select(LmsNotification).where(LmsNotification.user_id == user_id, LmsNotification.read_at.is_(None)))).scalars().all()
    now = utc_now()
    for item in rows: item.read_at = now
    await db.commit(); return await list_notifications(db, user_id)


async def generate_assignment_followups(db: AsyncSession, now: datetime) -> int:
    """Send one deadline roster and one overdue grade-release reminder per assignment."""
    created = 0
    rows = (await db.execute(
        select(LmsCourseworkAssignment, LmsCourse)
        .join(LmsCourse, LmsCourse.course_id == LmsCourseworkAssignment.course_id)
        .outerjoin(LmsExam, LmsExam.assignment_id == LmsCourseworkAssignment.assignment_id)
        .where(
            LmsCourseworkAssignment.status.in_(("published", "closed")),
            LmsCourseworkAssignment.due_at.is_not(None),
            LmsCourseworkAssignment.due_at <= now,
            or_(LmsExam.exam_id.is_(None), LmsExam.assessment_kind == "graded_mcq"),
        )
    )).all()
    for assignment, course in rows:
        lecturers = await _assignment_lecturer_ids(db, assignment)
        if not lecturers:
            continue
        submissions = (await db.execute(select(LmsCourseworkSubmission).where(
            LmsCourseworkSubmission.assignment_id == assignment.assignment_id,
        ))).scalars().all()
        due_at = assignment.due_at if assignment.due_at.tzinfo else assignment.due_at.replace(tzinfo=timezone.utc)
        action_url = f"{settings.LMS_UI_URL.rstrip('/')}?view=assignments"
        if due_at >= now - timedelta(days=1):
            unfinished = any(
                item.status == "in_progress"
                and (item.started_at if item.started_at.tzinfo else item.started_at.replace(tzinfo=timezone.utc)) < due_at
                for item in submissions
            )
            if not unfinished:
                student_ids = set(await _student_audience_user_ids(db, assignment.target_type, assignment.target_id))
                users = (await db.execute(select(User.user_id, User.full_name, User.email).where(User.user_id.in_(student_ids)))).all() if student_ids else []
                names = {user_id: name or email for user_id, name, email in users}
                submitted_ids = {item.student_user_id for item in submissions if item.status in {"submitted", "reviewed", "returned"} and item.submitted_at is not None}
                submitted = sorted((names[user_id] for user_id in student_ids & submitted_ids if user_id in names), key=str.casefold)
                missing = sorted((names[user_id] for user_id in student_ids - submitted_ids if user_id in names), key=str.casefold)
                target_class = await db.get(LmsClass, assignment.target_id) if assignment.target_type == "class" else None
                class_name = target_class.name if target_class else "Entire course"
                message = (
                    f"{course.code} · {class_name}\nDeadline: {local_time(due_at)}\n"
                    f"Submitted ({len(submitted)}): {', '.join(submitted) or 'None'}\n"
                    f"Not submitted ({len(missing)}): {', '.join(missing) or 'None'}\n"
                    "Open Assignments to review and mark the submitted work."
                )
                created += await _enqueue(
                    db, lecturers, f"assignment:{assignment.assignment_id}:deadline-summary:{due_at.isoformat()}",
                    "assignment_deadline_summary", _assignment_title("Submission summary", assignment.title),
                    message, action_url, "important", now, True,
                )
        if assignment.grades_released:
            continue
        finished = [item for item in submissions if item.status in {"submitted", "reviewed", "returned", "expired"}]
        if not finished or any(item.marks_awarded is None or item.marked_at is None for item in finished):
            continue
        latest_mark = max(item.marked_at if item.marked_at.tzinfo else item.marked_at.replace(tzinfo=timezone.utc) for item in finished)
        if now < latest_mark + timedelta(days=2):
            continue
        created += await _enqueue(
            db, lecturers, f"assignment:{assignment.assignment_id}:release-reminder:{latest_mark.isoformat()}",
            "assignment_release_reminder", _assignment_title("Release results", assignment.title),
            f"All {len(finished)} completed attempts for {course.code} · {assignment.title} have been marked. Results have remained unreleased for two days. Open Assignments and release the results to students.",
            action_url, "important", now, True,
        )
    return created


async def generate_reminders(db: AsyncSession, now: datetime | None = None) -> tuple[int, int]:
    now = now or utc_now(); created = 0; published = 0
    announcements = (await db.execute(select(LmsAnnouncement).where(LmsAnnouncement.status == "scheduled", LmsAnnouncement.publish_at <= now))).scalars().all()
    for item in announcements: created += await materialize_announcement(db, item, now); published += 1
    expiring = (await db.execute(select(LmsAnnouncement).where(LmsAnnouncement.status == "published", LmsAnnouncement.expires_at.is_not(None), LmsAnnouncement.expires_at <= now))).scalars().all()
    for item in expiring: item.status = "expired"

    assignment_rows = (await db.execute(select(LmsCourseworkAssignment, LmsCourse).join(LmsCourse, LmsCourse.course_id == LmsCourseworkAssignment.course_id).outerjoin(LmsExam, LmsExam.assignment_id == LmsCourseworkAssignment.assignment_id).where(LmsCourseworkAssignment.status == "published", or_(LmsCourseworkAssignment.available_from.is_(None), LmsCourseworkAssignment.available_from <= now), LmsCourseworkAssignment.due_at.is_not(None), LmsCourseworkAssignment.due_at > now, LmsCourseworkAssignment.due_at <= now + timedelta(minutes=max(ASSIGNMENT_REMINDERS) + 10), LmsExam.exam_id.is_(None)))).all()
    for assignment, course in assignment_rows:
        users = await _student_audience_user_ids(db, assignment.target_type, assignment.target_id)
        for offset in ASSIGNMENT_REMINDERS:
            if reminder_due(assignment.due_at, offset, now):
                label = "24 hours" if offset == 1440 else "1 hour"
                key = f"assignment:{assignment.assignment_id}:due:{assignment.due_at.isoformat()}:{offset}"
                created += await _enqueue(db, users, key, "assignment_deadline", f"Assignment due in {label}: {assignment.title}", f"{course.code} assignment is due on {local_time(assignment.due_at)}.", f"{settings.LMS_UI_URL.rstrip('/')}?view=assignments", "important" if offset == 60 else "normal", now, True, event_at=assignment.due_at)

    exam_rows = (await db.execute(select(LmsExam, LmsCourse).join(LmsCourse, LmsCourse.course_id == LmsExam.course_id).where(LmsExam.status == "published", or_(LmsExam.available_from.is_(None), LmsExam.available_from <= now), LmsExam.due_at.is_not(None), LmsExam.due_at > now, LmsExam.due_at <= now + timedelta(minutes=max(ASSIGNMENT_REMINDERS) + 10)))).all()
    for exam, course in exam_rows:
        users = await _student_audience_user_ids(db, exam.target_type, exam.target_id)
        for offset in ASSIGNMENT_REMINDERS:
            if reminder_due(exam.due_at, offset, now):
                label = "24 hours" if offset == 1440 else "1 hour"
                key = f"exam:{exam.exam_id}:due:{exam.due_at.isoformat()}:{offset}"
                created += await _enqueue(db, users, key, "exam_deadline", f"Exam deadline in {label}: {exam.title}", f"{course.code} assessment is due on {local_time(exam.due_at)}. Your timer cannot be reset after starting.", f"{settings.LMS_UI_URL.rstrip('/')}?view={'assignments' if exam.assessment_kind == 'graded_mcq' else 'exams'}", "important" if offset == 60 else "normal", now, True, event_at=exam.due_at)

    meeting_rows = (await db.execute(select(OnlineMeeting, LmsClass, LmsCourse).join(LmsClass, LmsClass.class_id == OnlineMeeting.class_id).join(LmsCourse, LmsCourse.course_id == LmsClass.course_id).where(OnlineMeeting.status == "scheduled", OnlineMeeting.start_time >= now - timedelta(minutes=10), OnlineMeeting.start_time <= now + timedelta(minutes=max(MEETING_REMINDERS) + 10)))).all()
    for meeting, class_, course in meeting_rows:
        class_ids = list((await db.execute(text("SELECT class_id FROM lms_meeting_audience_classes WHERE meeting_id=:id"), {"id": meeting.meeting_id})).scalars().all()) or [meeting.class_id]
        users = set()
        for class_id in class_ids: users.update(await _audience_user_ids(db, "class", class_id))
        users.add(meeting.lecturer_user_id)
        for offset in MEETING_REMINDERS:
            if reminder_due(meeting.start_time, offset, now):
                label = "now" if offset == 0 else ("24 hours" if offset == 1440 else "15 minutes")
                title = f"Join online class now: {meeting.title}" if offset == 0 else f"Online class in {label}: {meeting.title}"
                key = f"meeting:{meeting.meeting_id}:start:{meeting.start_time.isoformat()}:{offset}"
                audience = getattr(meeting, "audience_label", None) or f"{course.code} · {class_.name}"
                created += await _enqueue(db, list(users), key, "class_reminder", title, f"{audience} starts on {local_time(meeting.start_time)}. Use the LMS Join class button to enter.", f"{settings.LMS_UI_URL.rstrip('/')}?view=meetings", "urgent" if offset <= 15 else "important", now, True, event_at=meeting.start_time)
    created += await generate_assignment_followups(db, now)
    await db.commit(); return created, published


async def deliver_pending_emails(db: AsyncSession, now: datetime | None = None, limit: int = 100) -> tuple[int, int]:
    now = now or utc_now(); sent = failed = 0
    rows = (await db.execute(select(LmsNotification, User).join(User, User.user_id == LmsNotification.user_id).where(LmsNotification.email_enabled.is_(True), LmsNotification.email_status.in_(("pending", "failed")), LmsNotification.email_attempts < 3, LmsNotification.scheduled_for <= now).order_by(LmsNotification.scheduled_for).limit(limit))).all()
    for item, user in rows:
        result = await send_notification_email(
            user.email,
            user.full_name or user.email,
            item.title,
            item.message,
            item.action_url,
            item.event_key,
            notification_type=item.notification_type,
        )
        item.email_attempts += 1
        if result.sent:
            item.email_status = "sent"; item.email_sent_at = utc_now(); item.email_provider_id = result.provider_message_id; item.email_error = None; sent += 1
        else:
            item.email_status = "failed"; item.email_error = (result.error or "Email delivery failed")[:500]; failed += 1
        await db.commit()
    return sent, failed


async def dispatch_cycle(db: AsyncSession, now: datetime | None = None) -> NotificationDispatchSummary:
    from app.modules.lms import coursework_service, exam_service

    try:
        await coursework_service.finalize_due_drafts(db, now)
    except Exception:
        await db.rollback()
        logger.exception("Could not finalize due assignment drafts")
    try:
        await exam_service.finalize_expired_graded_attempts(db, now)
    except Exception:
        await db.rollback()
        logger.exception("Could not finalize expired assignment quizzes")
    # This worker is also the durable retry point for Vimeo's asynchronous
    # transcoding thumbnails. It makes newly uploaded lecture previews appear
    # even when the first upload response arrived before Vimeo was ready.
    try:
        from app.modules.lms import vimeo_service

        await vimeo_service.refresh_pending_learning_item_thumbnails(db)
    except Exception:
        # Thumbnail processing must never delay notification delivery.
        pass
    from app.modules.lms import zoom_service

    try:
        await zoom_service.process_jobs(db)
    except Exception:
        # Zoom reports and recordings can remain pending until the next worker cycle.
        pass
    try:
        await zoom_service.sweep_finished_meetings(db, now or utc_now())
    except Exception:
        # The next cycle retries meetings whose end webhook was missed.
        pass
    # Attendance is normally imported from the meeting.ended webhook. This sweep
    # is the safety net for a webhook that never arrived, so a finished class is
    # still collected without anyone clicking sync.
    try:
        await zoom_service.sweep_missing_attendance(db, now or utc_now())
    except Exception:
        pass
    try:
        await zoom_service.sweep_mismatched_attendance(db, now or utc_now())
    except Exception:
        pass
    try:
        await zoom_service.sweep_staff_attendance(db, now or utc_now())
    except Exception:
        pass
    try:
        await zoom_service.purge_expired_cloud_recordings(db, now or utc_now())
    except Exception:
        # Retention cleanup retries on the next cycle.
        pass
    created, published = await generate_reminders(db, now)
    sent, failed = await deliver_pending_emails(db, now)
    return NotificationDispatchSummary(reminders_created=created, announcements_published=published, emails_sent=sent, emails_failed=failed)
