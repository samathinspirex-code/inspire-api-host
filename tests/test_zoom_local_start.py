"""Zoom must get the class start as wall-clock time in the meeting's time zone."""
from datetime import datetime, timezone

from app.modules.lms.zoom_service import _zoom_local_start


def test_utc_instant_becomes_sri_lanka_wall_clock_time():
    # 7:00 AM in Colombo is 01:30 UTC; Zoom must receive "07:00" with Asia/Colombo.
    start = datetime(2026, 11, 20, 1, 30, tzinfo=timezone.utc)
    assert _zoom_local_start(start, "Asia/Colombo") == "2026-11-20T07:00:00"


def test_no_utc_offset_is_sent():
    start = datetime(2026, 11, 19, 10, 30, tzinfo=timezone.utc)
    value = _zoom_local_start(start, "Asia/Colombo")
    assert value == "2026-11-19T16:00:00"
    assert "+" not in value and not value.endswith("Z")


def test_naive_value_is_treated_as_utc_and_bad_zone_falls_back():
    assert _zoom_local_start(datetime(2026, 11, 20, 1, 30), "Asia/Colombo") == "2026-11-20T07:00:00"
    assert _zoom_local_start(datetime(2026, 11, 20, 1, 30, tzinfo=timezone.utc), "Not/AZone") == "2026-11-20T01:30:00"
