from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models.user import User
from app.modules.cms.models.activity_log import CmsActivityLog
from app.modules.cms.schemas.activity_log import ActivityLogItem, ActivityLogMetrics, ActivityLogResponse


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
    filters = []
    if search:
        term = f"%{search.strip()}%"
        filters.append(or_(
            CmsActivityLog.actor_name.ilike(term),
            CmsActivityLog.actor_email.ilike(term),
            CmsActivityLog.action.ilike(term),
            CmsActivityLog.module.ilike(term),
        ))

    statement = select(CmsActivityLog).order_by(CmsActivityLog.occurred_at.desc()).limit(size)
    count_statement = select(func.count(CmsActivityLog.activity_log_id))
    if filters:
        statement = statement.where(*filters)
        count_statement = count_statement.where(*filters)

    records = (await db.execute(statement)).scalars().all()
    total = int((await db.execute(count_statement)).scalar_one())
    start_of_today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    metric_row = (await db.execute(
        select(
            func.count(CmsActivityLog.activity_log_id).filter(CmsActivityLog.occurred_at >= start_of_today),
            func.count(CmsActivityLog.activity_log_id).filter(
                CmsActivityLog.occurred_at >= start_of_today,
                CmsActivityLog.module == "Users",
            ),
            func.count(CmsActivityLog.activity_log_id).filter(
                CmsActivityLog.occurred_at >= start_of_today,
                CmsActivityLog.is_sensitive.is_(True),
            ),
        )
    )).one()

    return ActivityLogResponse(
        data=[ActivityLogItem(
            activity_log_id=item.activity_log_id,
            actor_name=item.actor_name,
            actor_email=item.actor_email,
            action=item.action,
            module=item.module,
            result=item.result,
            is_sensitive=item.is_sensitive,
            occurred_at=item.occurred_at,
        ) for item in records],
        total=total,
        metrics=ActivityLogMetrics(
            events_today=int(metric_row[0] or 0),
            user_changes_today=int(metric_row[1] or 0),
            sensitive_actions_today=int(metric_row[2] or 0),
        ),
    )
