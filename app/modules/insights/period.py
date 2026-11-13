"""Sri Lanka calendar windows used by CRM reporting screens."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException


def period_bounds(period: str, start_date: date | None = None, end_date: date | None = None) -> tuple[datetime | None, datetime | None]:
    today = datetime.now(ZoneInfo("Asia/Colombo")).date()
    if period == "all":
        return None, None
    if period == "today":
        start = end = today
    elif period == "yesterday":
        start = end = today - timedelta(days=1)
    elif period == "month":
        start, end = today.replace(day=1), today
    elif period == "range":
        if not start_date or not end_date or start_date > end_date:
            raise HTTPException(status_code=422, detail="Choose a valid start and end date")
        start, end = start_date, end_date
    else:
        raise HTTPException(status_code=422, detail="Unknown date filter")
    local = ZoneInfo("Asia/Colombo")
    first = datetime.combine(start, time.min, local).astimezone(timezone.utc).replace(tzinfo=None)
    after = datetime.combine(end + timedelta(days=1), time.min, local).astimezone(timezone.utc).replace(tzinfo=None)
    return first, after
