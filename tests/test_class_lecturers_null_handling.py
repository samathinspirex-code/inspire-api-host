import pytest
from app.modules.lms.schemas.assignment import AssignmentPersonItem, AssignmentListResponse
from app.modules.lms.assignment_service import _lecturer_item, _student_item
from types import SimpleNamespace
from datetime import datetime, timezone

def test_lecturer_item_with_null_staff_number():
    user = SimpleNamespace(user_id=57, full_name="Sumaiya Iqbal", email="sumaiya@inspire.college")
    profile = SimpleNamespace(staff_number=None, job_title="Academic Head", profile_image_url=None)
    relation = SimpleNamespace(assigned_at=datetime.now(timezone.utc))

    item = _lecturer_item(user, profile, relation)
    assert item.user_id == 57
    assert item.full_name == "Sumaiya Iqbal"
    assert item.reference_number == "—"
    assert item.secondary_label == "Academic Head"

def test_lecturer_item_with_null_profile():
    user = SimpleNamespace(user_id=99, full_name="No Profile Lecturer", email="no.profile@inspire.college")
    profile = None
    relation = SimpleNamespace(assigned_at=datetime.now(timezone.utc))

    item = _lecturer_item(user, profile, relation)
    assert item.user_id == 99
    assert item.reference_number == "—"
    assert item.secondary_label is None
