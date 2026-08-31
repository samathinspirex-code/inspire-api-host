from app.modules.lms.analytics_service import _average, _distribution, _percentage


def test_analytics_percentages_are_safe_and_rounded():
    assert _percentage(7, 9) == 77.8
    assert _percentage(0, 0) is None
    assert _average([70, 80, 90]) == 80


def test_grade_distribution_keeps_every_result_in_one_band():
    items = _distribution([92, 77, 61, 34])
    assert [item.value for item in items] == [1, 1, 1, 1]
    assert sum(item.percentage for item in items) == 100
