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


def test_avatar_choice_accepts_only_bundled_avatars():
    from app.modules.lms.schemas.profile import DEFAULT_AVATAR_KEYS, ProfileAvatarUpdate, default_avatar_url

    assert len(DEFAULT_AVATAR_KEYS) == 12
    assert ProfileAvatarUpdate(avatar_key="ocean").avatar_key == "ocean"
    assert default_avatar_url("ocean") == "/avatars/ocean.svg"
    for bad in ("unknown", "../etc", "https://evil.example/x.svg", ""):
        with pytest.raises(ValidationError):
            ProfileAvatarUpdate(avatar_key=bad)


def test_bundled_avatar_files_match_api_keys():
    from pathlib import Path
    from app.modules.lms.schemas.profile import DEFAULT_AVATAR_KEYS

    avatars = Path(__file__).resolve().parents[2] / "LMS-UI" / "public" / "avatars"
    if not avatars.exists():
        pytest.skip("LMS-UI checkout not next to the API")
    assert sorted(p.stem for p in avatars.glob("*.svg")) == sorted(DEFAULT_AVATAR_KEYS)
