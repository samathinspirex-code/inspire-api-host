from datetime import datetime

from pydantic import BaseModel


class ActivityLogItem(BaseModel):
    activity_log_id: int
    actor_name: str
    actor_email: str | None = None
    action: str
    module: str
    target: str | None = None
    result: str
    is_sensitive: bool
    occurred_at: datetime


class ActivityLogMetrics(BaseModel):
    events_last_7_days: int
    user_changes_last_7_days: int
    sensitive_actions_last_7_days: int


class ActivityLogResponse(BaseModel):
    data: list[ActivityLogItem]
    total: int
    metrics: ActivityLogMetrics
