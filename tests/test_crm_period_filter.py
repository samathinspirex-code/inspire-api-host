from datetime import date, datetime

import pytest
from fastapi import HTTPException

from app.modules.insights.period import period_bounds


def test_date_range_uses_sri_lanka_day_boundaries():
    start, end = period_bounds("range", date(2026, 11, 1), date(2026, 11, 2))
    assert start == datetime(2026, 10, 31, 18, 30)
    assert end == datetime(2026, 11, 2, 18, 30)


def test_all_has_no_bounds_and_invalid_range_is_rejected():
    assert period_bounds("all") == (None, None)
    with pytest.raises(HTTPException):
        period_bounds("range", date(2026, 11, 3), date(2026, 11, 2))
