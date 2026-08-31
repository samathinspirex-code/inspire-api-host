from app.modules.lms.profile_service import calculate_profile_completeness


def test_profile_completeness_counts_only_meaningful_values():
    assert calculate_profile_completeness(["Samath", "  ", None, "Colombo"]) == 50


def test_profile_completeness_handles_empty_definition():
    assert calculate_profile_completeness([]) == 0


def test_profile_completeness_can_reach_one_hundred():
    assert calculate_profile_completeness(["One", "Two", "Three"]) == 100
