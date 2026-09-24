from datetime import datetime, timezone

from app.modules.lms.analytics_service import _average, _distribution, _percentage, build_report_csv
from app.modules.lms.schemas.analytics import (
    AnalyticsCourseInsight,
    AnalyticsDashboardResponse,
    AnalyticsMetric,
    AnalyticsTrendPoint,
)


def test_analytics_percentages_are_safe_and_rounded():
    assert _percentage(7, 9) == 77.8
    assert _percentage(0, 0) is None
    assert _average([70, 80, 90]) == 80


def test_grade_distribution_keeps_every_result_in_one_band():
    items = _distribution([92, 77, 61, 34])
    assert [item.value for item in items] == [1, 1, 1, 1]
    assert sum(item.percentage for item in items) == 100


def test_academic_report_csv_includes_metrics_courses_and_trend():
    report = AnalyticsDashboardResponse(
        role="ADMIN",
        generated_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        engagement_score=72,
        engagement_label="Steady",
        metrics=[AnalyticsMetric(key="progress", label="Average progress", value=50, display_value="50%", hint="Required content")],
        weekly_trend=[AnalyticsTrendPoint(label="22 Sep", activity=4, completions=1)],
        grade_distribution=[],
        attendance_distribution=[],
        course_insights=[AnalyticsCourseInsight(
            course_id=1, course_code="MR101", course_title="Market Research",
            students=12, progress=50, attendance=80, grade_average=None,
        )],
    )
    csv_text = build_report_csv(report)
    assert "Average progress,50%,Required content" in csv_text
    assert "MR101,Market Research,12,50.0,80.0," in csv_text
    assert "22 Sep,4,1" in csv_text
