import pytest

from app.core.errors import ValidationError
from app.modules.crm.models.lead import CrmLead
from app.modules.crm.service import PIPELINE_STAGES, _apply_course_choice, _validate_lead_state


def _lead(email="student@example.com"):
    return CrmLead(full_name="Test Student", email=email, stage="new_lead")


def test_statuses_match_the_new_workflow():
    assert PIPELINE_STAGES == [
        "new_lead", "uncontactable", "contactable", "future_prospect",
        "not_interested", "lost_to_competitor", "cant_afford", "enrolled",
    ]


def test_outcome_requires_reason_and_other_requires_remarks():
    with pytest.raises(ValidationError):
        _validate_lead_state(_lead(), {"stage": "not_interested"})
    with pytest.raises(ValidationError):
        _validate_lead_state(_lead(), {"stage": "lost_to_competitor", "status_reason": "Other"})
    _validate_lead_state(_lead(), {
        "stage": "lost_to_competitor", "status_reason": "Other", "status_remarks": "Specific reason",
    })


def test_enrollment_requires_valid_email():
    with pytest.raises(ValidationError, match="Email"):
        _validate_lead_state(_lead(email=None), {"stage": "enrolled"})
    _validate_lead_state(_lead(), {"stage": "enrolled"})


def test_catalogue_values_fill_missing_terms_without_overwriting_lead_price():
    choice = {
        "title": "Business", "programme_name": "ATHE", "awarding_body": "ATHE",
        "school_name": "School of Business", "programme_fee": 100000, "duration": "12 months",
    }
    data = {"programme_fee": 90000, "programme_duration": None}
    _apply_course_choice(data, choice)
    assert data["programme_fee"] == 90000
    assert data["programme_duration"] == "12 months"
    assert data["interested_course"] == "Business"
