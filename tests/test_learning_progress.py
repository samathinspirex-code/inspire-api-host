from datetime import datetime, timedelta, timezone

from app.modules.lms.progress_service import (
    allowed_watch_delta,
    completion_percent,
    continuous_watched_seconds,
    video_completion_state,
)


def test_completion_percent_is_bounded_and_rounded():
    assert completion_percent(45, 100) == 45
    assert completion_percent(1, 3) == 33.33
    assert completion_percent(150, 100) == 100
    assert completion_percent(20, None) == 0


def test_first_progress_heartbeat_has_a_small_allowance():
    now = datetime.now(timezone.utc)
    assert allowed_watch_delta(60, None, now) == 15


def test_progress_delta_cannot_grow_faster_than_elapsed_time():
    now = datetime.now(timezone.utc)
    previous = now - timedelta(seconds=8)
    assert allowed_watch_delta(60, previous, now) == 10


def test_progress_delta_has_a_hard_cap():
    now = datetime.now(timezone.utc)
    previous = now - timedelta(minutes=5)
    assert allowed_watch_delta(60, previous, now) == 30


def test_forward_seek_cannot_inflate_progress():
    assert continuous_watched_seconds(20, 100, 5, 120) == 25


def test_rewatching_content_does_not_inflate_progress():
    assert continuous_watched_seconds(20, 10, 5, 120) == 20


def test_normal_playback_advances_to_the_current_position():
    assert continuous_watched_seconds(20, 25, 5, 120) == 25


def test_ended_video_is_stored_as_exactly_complete():
    assert video_completion_state(119, 120, "ended") == (120, 100.0, True)


def test_video_cannot_be_completed_by_ending_before_it_was_watched():
    assert video_completion_state(80, 120, "ended") == (80, 66.67, False)


def test_video_is_not_completed_until_the_player_ends():
    assert video_completion_state(119, 120, "heartbeat") == (119, 99.17, False)


def test_completed_video_cannot_fall_below_one_hundred_percent():
    assert video_completion_state(30, 120, "heartbeat", True) == (120, 100.0, True)
