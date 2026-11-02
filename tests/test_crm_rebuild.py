import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import ValidationError
from app.modules.crm.models.lead import CrmLead
from app.modules.crm import service
from app.modules.crm.service import PIPELINE_STAGES, _apply_course_choice, _legacy_outcome_details, _validate_lead_state


def _lead(email="student@example.com"):
    return CrmLead(full_name="Test Student", email=email, stage="new_lead")


def test_statuses_match_the_new_workflow():
    assert PIPELINE_STAGES == [
        "new_lead", "uncontactable", "contacted", "follow_up", "potential",
        "future_prospect", "not_interested", "enrolled",
    ]


@pytest.mark.parametrize("stage", ["follow_up", "potential"])
def test_follow_up_and_potential_require_a_note(stage):
    with pytest.raises(ValidationError, match="Enter a note"):
        _validate_lead_state(_lead(), {"stage": stage, "status_remarks": "  "})
    _validate_lead_state(_lead(), {"stage": stage, "status_remarks": "Interested after exam results"})


@pytest.mark.parametrize("old_stage,expected_reason", [
    ("lost_to_competitor", "Joined Another Institute"),
    ("cant_afford", "Fees Beyond Expectations"),
])
def test_legacy_outcomes_keep_original_details(old_stage, expected_reason):
    lead = CrmLead(full_name="Legacy", stage=old_stage, status_reason="Original reason", affordability_reason="Needs discount", status_remarks="Original remark")
    reason, note = _legacy_outcome_details(lead)
    assert reason == expected_reason
    assert old_stage in note
    assert "Original reason" in note
    assert "Needs discount" in note
    assert "Original remark" in note


@pytest.mark.parametrize("stage", ["follow_up", "potential"])
def test_stage_update_rejects_missing_note_before_saving(stage):
    async def run():
        lead = CrmLead(lead_id=1, full_name="Test Student", email="student@example.com", stage="new_lead")
        repository = AsyncMock()
        repository.get.return_value = lead
        with patch.object(service, "CrmLeadRepository", return_value=repository):
            with pytest.raises(ValidationError, match="Enter a note"):
                await service.update_stage(AsyncMock(), 1, stage)
        repository.update.assert_not_awaited()

    asyncio.run(run())


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
