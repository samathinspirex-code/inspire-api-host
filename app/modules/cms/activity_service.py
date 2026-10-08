from datetime import timedelta
import re

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models.user import User
from app.modules.cms.models.activity_log import CmsActivityLog
from app.modules.cms.schemas.activity_log import ActivityLogItem, ActivityLogMetrics, ActivityLogResponse


RETENTION = timedelta(days=7)


def _cutoff():
    # The database stores TIMESTAMPTZ values in UTC; use its clock for expiry.
    return func.now() - RETENTION


def _target(path: str) -> str | None:
    for pattern, label in (
        (r"/api/v1/lms/meetings/(\d+)(?:/.*)?", "Meeting"),
        (r"/api/v1/lms/exams/(\d+)(?:/.*)?", "Exam"),
        (r"/api/v1/lms/profile/media/(\d+)/complete", "Photo"),
        (r"/api/v1/cms/crm/leads/(\d+)(?:/.*)?", "Lead"),
        (r"/api/v1/cms/media/(\d+)/complete", "Media"),
    ):
        match = re.fullmatch(pattern, path)
        if match:
            return f"{label} #{match.group(1)}"
    if path.startswith("/api/v1/lms/profile"):
        return "Own profile"
    return None


async def record_successful_change(
    db: AsyncSession,
    *,
    actor_user_id: int,
    actor_email: str,
    method: str,
    path: str,
    action: str,
    module: str,
    is_sensitive: bool,
) -> None:
    """Write audit data in its own session so logging never interrupts the action."""
    actor_name = actor_email
    user = await db.get(User, actor_user_id)
    if user and user.full_name:
        actor_name = user.full_name
    await db.execute(delete(CmsActivityLog).where(CmsActivityLog.occurred_at < _cutoff()))
    db.add(CmsActivityLog(
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        actor_email=actor_email,
        action=action,
        module=module,
        request_method=method,
        request_path=path,
        result="Success",
        is_sensitive=is_sensitive,
    ))
    await db.commit()


async def list_activity_log(db: AsyncSession, search: str | None, size: int) -> ActivityLogResponse:
    # Expire rows physically when the log is opened, and filter by age as well
    # so a row never remains visible past seven days between cleanup runs.
    await db.execute(delete(CmsActivityLog).where(CmsActivityLog.occurred_at < _cutoff()))
    await db.commit()
    filters = [CmsActivityLog.occurred_at >= _cutoff()]
    if search:
        term = f"%{search.strip()}%"
        filters.append(or_(
            CmsActivityLog.actor_name.ilike(term),
            CmsActivityLog.actor_email.ilike(term),
            CmsActivityLog.action.ilike(term),
            CmsActivityLog.module.ilike(term),
            CmsActivityLog.request_path.ilike(term),
        ))

    statement = select(CmsActivityLog).order_by(CmsActivityLog.occurred_at.desc()).limit(size)
    count_statement = select(func.count(CmsActivityLog.activity_log_id))
    statement = statement.where(*filters)
    count_statement = count_statement.where(*filters)

    records = (await db.execute(statement)).scalars().all()
    total = int((await db.execute(count_statement)).scalar_one())
    metric_row = (await db.execute(
        select(
            func.count(CmsActivityLog.activity_log_id),
            func.count(CmsActivityLog.activity_log_id).filter(
                CmsActivityLog.module == "Users",
            ),
            func.count(CmsActivityLog.activity_log_id).filter(
                CmsActivityLog.is_sensitive.is_(True),
            ),
        ).where(CmsActivityLog.occurred_at >= _cutoff())
    )).one()

    # Older rows retain their original text in storage, but the API renders
    # them from the recorded method and path with the current classifications.
    from app.core.activity_audit import _describe_change

    data = []
    for item in records:
        description = _describe_change(item.request_method, item.request_path)
        data.append(ActivityLogItem(
            activity_log_id=item.activity_log_id,
            actor_name=item.actor_name,
            actor_email=item.actor_email,
            action=description[0] if description else item.action,
            module=description[1] if description else item.module,
            target=_target(item.request_path),
            result=item.result,
            is_sensitive=description[2] if description else item.is_sensitive,
            occurred_at=item.occurred_at,
        ))

    return ActivityLogResponse(
        data=data,
        total=total,
        metrics=ActivityLogMetrics(
            events_last_7_days=int(metric_row[0] or 0),
            user_changes_last_7_days=int(metric_row[1] or 0),
            sensitive_actions_last_7_days=int(metric_row[2] or 0),
        ),
    )
