import csv
import io
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from fastapi import Request

from app.modules.crm.models.lead import CrmLead
from app.modules.crm.models.activity import CrmActivity
from app.modules.crm.schemas import (
    CrmActivityCreate,
    CrmLeadCreate,
    CrmLeadUpdate,
    CrmStageUpdate,
)
from app.modules.crm.public_router import extract_client_location
import app.modules.crm.service as crm_service


# ─────────────────────────────────────────────────────────────
# 1. Location Detection QA Tests (Secondary Location Approach)
# ─────────────────────────────────────────────────────────────
def test_location_extracted_from_cloudflare_headers():
    """Verify silent IP geolocation captures City & Country when user leaves it blank."""
    scope = {
        "type": "http",
        "headers": [
            (b"cf-ipcity", b"Kandy"),
            (b"cf-ipcountry", b"LK"),
            (b"cf-iplatitude", b"7.2906"),
            (b"cf-iplongitude", b"80.6337"),
        ],
    }
    request = Request(scope)
    city, district, country, lat, lng = extract_client_location(
        request, user_city=None, user_district=None, user_lat=None, user_lng=None
    )
    assert city == "Kandy"
    assert country == "LK"
    assert lat == 7.2906
    assert lng == 80.6337


def test_location_user_override_over_ip():
    """Verify that if user explicitly selects or types their city, it takes precedence over IP."""
    scope = {
        "type": "http",
        "headers": [
            (b"cf-ipcity", b"Colombo"),
            (b"cf-ipcountry", b"LK"),
        ],
    }
    request = Request(scope)
    city, district, country, lat, lng = extract_client_location(
        request, user_city="Galle", user_district="Southern Province"
    )
    assert city == "Galle"
    assert district == "Southern Province"
    assert country == "LK"


def test_location_default_fallback():
    """Verify default fallback to 'Sri Lanka' when no headers exist."""
    scope = {"type": "http", "headers": []}
    request = Request(scope)
    city, district, country, lat, lng = extract_client_location(request)
    assert city is None
    assert country == "Sri Lanka"
    assert lat is None
    assert lng is None


# ─────────────────────────────────────────────────────────────
# 2. Schema & Excel Field Compatibility QA
# ─────────────────────────────────────────────────────────────
def test_lead_schema_with_all_excel_fields():
    """Verify CrmLeadCreate and CrmLeadUpdate accept all 48 columns from the user's Excel sheet."""
    lead_input = {
        "full_name": "Samindi Weerasiri",
        "phone": "076-694220",
        "whatsapp": "760694220",
        "email": "samindi.inspirex@gmail.com",
        "city": "Colombo",
        "district": "Western",
        "school": "Helena Girls's School",
        "nationality": "Sri Lankan",
        "faculty": "School of Computing",
        "interested_programme": "School of Computing",
        "interested_course": "Level 3 Diploma in Smart Computing",
        "highest_qualification": "After O/L ( Pending Results)",
        "student_status": "Waiting for Parental Approval",
        "parents_occupation": "Accountant",
        "parents_email": "parents@test.com",
        "address_line1": "No 45",
        "address_line2": "Main Street",
        "country": "Sri Lanka",
        "source": "website_admission",
        "stage": "counselling",
        "priority": "high",
        "amount": 10000.0,
        "social_lead_id": "WA_987654",
    }
    payload = CrmLeadCreate(**lead_input)
    assert payload.full_name == "Samindi Weerasiri"
    assert payload.school == "Helena Girls's School"
    assert payload.amount == 10000.0
    assert payload.student_status == "Waiting for Parental Approval"
    assert payload.social_lead_id == "WA_987654"


# ─────────────────────────────────────────────────────────────
# 3. CSV Export Template QA (Inspire College / Zoho CRM Format)
# ─────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_csv_template_export_structure():
    """Verify that export_leads_csv with format='template' outputs all 48 exact headers."""
    class MockLeadRecord:
        lead_id = 731011
        assigned_counsellor_id = 701049
        counsellor_name = "Samindi Weerasiri"
        amount = 10000.0
        full_name = "Samindi test"
        followup_date = datetime(2025, 5, 8, tzinfo=timezone.utc)
        stage = "counselling"
        source = "website_admission"
        created_at = datetime(2025, 5, 3, 13, 39, 17, tzinfo=timezone.utc)
        updated_at = datetime(2025, 5, 3, 13, 39, 17, tzinfo=timezone.utc)
        notes = "Follow up with parents"
        message = ""
        social_lead_id = "3.1618269806392e+15"
        phone = "076-694220"
        whatsapp = "760694220"
        email = "samindi.inspirex@gmail.com"
        school = "Helena Girls's School"
        nationality = "Sri Lankan"
        faculty = "School of Computing"
        interested_programme = "School of Computing"
        interested_course = "Level 3 Diploma in Smart Computing"
        highest_qualification = "After O/L ( Pending Results)"
        student_status = "Waiting for Parental Approval"
        parents_occupation = "Civil Engineer"
        parents_email = "test@gmail.com"
        address_line1 = "123 Galle Road"
        address_line2 = "Colombo 03"
        city = "Colombo"
        country = "Sri Lanka"

    class FakeRepo:
        def __init__(self, db):
            pass
        async def list_export(self, stage, source):
            return [MockLeadRecord()]

    orig_repo = crm_service.CrmLeadRepository
    crm_service.CrmLeadRepository = FakeRepo

    try:
        csv_text = await crm_service.export_leads_csv(None, None, None, export_format="template")
        lines = list(csv.reader(io.StringIO(csv_text)))
        header = lines[0]
        data = lines[1]

        # 1. Total columns must equal 48
        assert len(header) == 48

        # 2. Check crucial Excel template columns
        expected_cols = [
            "Record Id", "Students Pipeline Owner.id", "Students Pipeline Owner",
            "Amount", "Students Pipeline Name", "Closing Date", "Stage",
            "Lead Source", "Contact Name", "Expected Revenue", "Social Lead ID",
            "Phone", "Email", "School", "Nationality", "Faculty  (Schools)",
            "Selected Program", "Student Highest Education Qualification",
            "Student Status", "Parents Occupation", "Parents Email",
            "Address Line 1", "Address Line 2", "City", "Country"
        ]
        for col in expected_cols:
            assert col in header, f"Missing expected column: {col}"

        # 3. Check data mapping
        assert data[header.index("Record Id")] == "zcrm_731011"
        assert data[header.index("Students Pipeline Owner")] == "Samindi Weerasiri"
        assert data[header.index("Students Pipeline Name")] == "Samindi test"
        assert data[header.index("Amount")] == "10000.0"
        assert data[header.index("School")] == "Helena Girls's School"
        assert data[header.index("Faculty  (Schools)")] == "School of Computing"
        assert data[header.index("City")] == "Colombo"
        assert data[header.index("Country")] == "Sri Lanka"
        assert data[header.index("Pipeline")] == "InspireX Pipeline"

    finally:
        crm_service.CrmLeadRepository = orig_repo


# ─────────────────────────────────────────────────────────────
# 4. Pipeline Stages & Kanban Logic QA
# ─────────────────────────────────────────────────────────────
def test_canonical_pipeline_stages_order():
    """Ensure the 9 Kanban pipeline stages match the admissions journey."""
    expected_order = [
        "new_inquiry",
        "contacted",
        "counselling",
        "application_started",
        "documents_pending",
        "app_submitted",
        "offer_sent",
        "enrolled",
        "lost_deferred",
    ]
    assert crm_service.PIPELINE_STAGES == expected_order
