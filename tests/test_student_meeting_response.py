"""Student meeting responses must not expose Zoom host links or fail validation."""

from datetime import datetime, timezone

from app.modules.lms.schemas import MeetingItem, MeetingListResponse


def test_student_meeting_response_accepts_hidden_provider_link():
    meeting = MeetingItem(
        meeting_id=1,
        class_id=10,
        class_code="CLASS-10",
        class_name="September intake",
        course_code="HND",
        course_title="Computing",
        title="Live class",
        description=None,
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        timezone="Asia/Colombo",
        status="scheduled",
        provider="zoom",
        join_uri=None,
        provider_meeting_id=None,
        students_notified=True,
        attendee_count=12,
        created_at=datetime.now(timezone.utc),
    )

    response = MeetingListResponse(data=[meeting])

    assert response.data[0].join_uri is None
    assert response.data[0].provider_meeting_id is None
