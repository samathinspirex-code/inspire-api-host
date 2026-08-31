from datetime import datetime

from pydantic import BaseModel


class AnalyticsMetric(BaseModel):
    key: str
    label: str
    value: float
    display_value: str
    hint: str
    tone: str = "purple"


class AnalyticsTrendPoint(BaseModel):
    label: str
    activity: int
    completions: int


class AnalyticsBreakdownItem(BaseModel):
    label: str
    value: int
    percentage: float
    tone: str


class AnalyticsCourseInsight(BaseModel):
    course_id: int
    course_code: str
    course_title: str
    students: int
    progress: float | None = None
    attendance: float | None = None
    grade_average: float | None = None


class AnalyticsDashboardResponse(BaseModel):
    role: str
    generated_at: datetime
    engagement_score: float
    engagement_label: str
    metrics: list[AnalyticsMetric]
    weekly_trend: list[AnalyticsTrendPoint]
    grade_distribution: list[AnalyticsBreakdownItem]
    attendance_distribution: list[AnalyticsBreakdownItem]
    course_insights: list[AnalyticsCourseInsight]
