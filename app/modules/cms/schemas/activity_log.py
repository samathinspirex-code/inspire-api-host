from datetime import datetime

from pydantic import BaseModel


class ActivityLogItem(BaseModel):
    activity_log_id: int
    actor_name: str
    actor_email: str | None = None
    action: str
    module: str
    result: str
    is_sensitive: bool
    occurred_at: datetime


class ActivityLogMetrics(BaseModel):
    events_today: int
    user_changes_today: int
    sensitive_actions_today: int


class ActivityLogResponse(BaseModel):
    data: list[ActivityLogItem]
    total: int
    metrics: ActivityLogMetrics
