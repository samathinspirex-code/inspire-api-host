from app.modules.lms.profile_service import calculate_profile_completeness
from app.modules.lms.schemas.profile import MyProfileUpdate
from pydantic import ValidationError
import pytest


def test_profile_completeness_counts_only_meaningful_values():
    assert calculate_profile_completeness(["Samath", "  ", None, "Colombo"]) == 50


def test_profile_completeness_handles_empty_definition():
    assert calculate_profile_completeness([]) == 0


def test_profile_completeness_can_reach_one_hundred():
    assert calculate_profile_completeness(["One", "Two", "Three"]) == 100


def test_profile_accepts_iana_timezone_and_rejects_invalid_zone():
    assert MyProfileUpdate(full_name="Student", country="United States", time_zone="America/New_York").time_zone == "America/New_York"
    with pytest.raises(ValidationError):
        MyProfileUpdate(full_name="Student", country="United States", time_zone="New York")
