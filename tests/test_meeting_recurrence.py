from datetime import datetime, timezone

from app.modules.lms.meeting_service import _occurrence_windows
from app.modules.lms.schemas import MeetingCreate
from app.modules.lms.zoom_service import _past_meeting_refs


def schedule(**recurrence):
    return MeetingCreate(
        title="Week 1",
        start_time=datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc),  # a Monday
        end_time=datetime(2026, 9, 28, 10, 30, tzinfo=timezone.utc),
        class_id=1,
        recurrence=recurrence or None,
    )


def test_a_single_class_produces_one_session():
    assert _occurrence_windows(schedule()) == [
        (datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc), datetime(2026, 9, 28, 10, 30, tzinfo=timezone.utc)),
    ]


def test_weekly_repeat_keeps_the_start_weekday_and_duration():
    windows = _occurrence_windows(schedule(frequency="weekly", occurrences=3))
    assert [start.date().isoformat() for start, _ in windows] == ["2026-09-28", "2026-10-05", "2026-10-12"]
    assert all((end - start).total_seconds() == 5400 for start, end in windows)


def test_weekly_repeat_can_run_on_several_days_in_order():
    windows = _occurrence_windows(schedule(frequency="weekly", occurrences=4, weekdays=[2, 0]))
    assert [start.date().isoformat() for start, _ in windows] == [
        "2026-09-28", "2026-09-30", "2026-10-05", "2026-10-07",
    ]


def test_fortnightly_repeat_skips_the_in_between_week():
    windows = _occurrence_windows(schedule(frequency="weekly", interval=2, occurrences=3))
    assert [start.date().isoformat() for start, _ in windows] == ["2026-09-28", "2026-10-12", "2026-10-26"]


def test_daily_repeat_advances_by_its_interval():
    windows = _occurrence_windows(schedule(frequency="daily", interval=3, occurrences=3))
    assert [start.date().isoformat() for start, _ in windows] == ["2026-09-28", "2026-10-01", "2026-10-04"]


def test_a_weekly_day_before_the_start_time_is_not_booked_in_the_past():
    windows = _occurrence_windows(schedule(frequency="weekly", occurrences=2, weekdays=[0, 4]))
    assert [start.date().isoformat() for start, _ in windows] == ["2026-09-28", "2026-10-02"]


def test_slash_prefixed_meeting_uuids_are_encoded_twice_for_zoom():
    refs = _past_meeting_refs({"provider_meeting_uuid": "/abcd==", "provider_meeting_id": "123"})
    assert refs[0] == "%252Fabcd%253D%253D"
    assert refs[1] == "123"


def test_a_plain_uuid_is_encoded_once():
    refs = _past_meeting_refs({"provider_meeting_uuid": "abcd==", "provider_meeting_id": "123"})
    assert refs[0] == "abcd%3D%3D"
