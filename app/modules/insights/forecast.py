"""Enrolment path from today's LMS students to the mission deadline."""

import math
from datetime import date


def mission_forecast(*, current: int, target: int, today: date, end: date, recent_monthly: float) -> dict:
    days_left = max(0, (end - today).days)
    months_left = max(1, math.ceil(days_left / 30.44)) if days_left else 1
    gap = max(0, target - current)
    need = math.ceil(gap / months_left) if gap else 0
    rate = max(0.0, recent_monthly)
    labels = []
    cursor = date(today.year, today.month, 1)
    for _ in range(months_left):
        month = cursor.month + 1
        year = cursor.year + (1 if month > 12 else 0)
        month = 1 if month > 12 else month
        cursor = date(year, month, 1)
        if cursor > end and labels:
            break
        labels.append(cursor.strftime("%b"))
        if cursor.year == end.year and cursor.month == end.month:
            break
    if not labels:
        labels = [end.strftime("%b")]
    new_students = [max(0, round(rate))] * len(labels)
    running = []
    total = current
    for value in new_students:
        total += value
        running.append(total)
    path = []
    walked = current
    for _ in labels:
        walked = min(target, walked + need)
        path.append(walked)
    landing = running[-1] if running else current
    short = max(0, target - landing)
    return {
        "days_left": days_left,
        "months_left": len(labels),
        "need_per_month": need,
        "months": labels,
        "new_students": new_students,
        "running": running,
        "path": path,
        "landing": landing,
        "short": short,
        "on_track": landing >= target,
    }
