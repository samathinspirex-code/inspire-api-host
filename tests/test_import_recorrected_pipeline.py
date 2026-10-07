from types import SimpleNamespace

import pytest

from app.modules.crm.schemas import validate_status_fields
from scripts.import_recorrected_pipeline import (
    group_rows,
    mapped_course,
    phone_parts,
    stage_fields,
)


def test_local_and_international_phone_normalization():
    assert phone_parts("0773168633")[:2] == ("94773168633", "+94773168633")
    assert phone_parts("+94773168633")[:2] == ("94773168633", "+94773168633")
    assert phone_parts("+97471311067")[:2] == ("97471311067", "+97471311067")


def test_two_rows_with_same_secondary_number_are_grouped():
    rows = [
        {"_phone_keys": {"94771111111", "94772222222"}},
        {"_phone_keys": {"94772222222"}},
        {"_phone_keys": {"94773333333"}},
    ]
    groups = group_rows(rows)
    assert sorted(len(group) for group in groups) == [1, 2]


def test_reviewed_unavailable_and_website_markers():
    assert mapped_course("Certificate in Robotics Programming (unavalable)") == (
        "Certificate in Robotics Programming (unavailable)", "Unavailable course"
    )
    assert mapped_course("HND in Accounting and Finance (need add to website)") == (
        "HND in Accounting and Finance", "Need add to website"
    )


def test_legacy_statuses_have_current_reasons():
    assert stage_fields("Waiting for Parental Approval")["delay_reason"] == "Need Parent / Sponsor Approval"
    assert stage_fields("Looking for Results")["delay_reason"] == "Waiting for Exam Results"
    assert stage_fields("Not Interested / Declined")["status_reason"] == "Other"
    assert stage_fields("Payment Done")["enrollment_email_status"] == "pending_email_legacy"


def test_blank_email_enrolled_exception_is_explicit():
    state = SimpleNamespace(
        stage="enrolled", email=None, status_reason=None, status_remarks=None,
        affordability_reason=None, delay_reason=None, legacy_enrolled_email_pending=True,
    )
    validate_status_fields(state)
    state.legacy_enrolled_email_pending = False
    with pytest.raises(ValueError, match="Email is required"):
        validate_status_fields(state)
