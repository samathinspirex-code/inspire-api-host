import pytest
from app.modules.crm.models.lead import CrmLead
from app.modules.crm.schemas import CrmLeadCreate, CrmLeadUpdate, CrmStageUpdate
from app.modules.crm import service


def test_crm_lead_create_schema():
    payload = CrmLeadCreate(
        full_name="Nawanjana Amandi",
        phone="0767023644",
        email="amandinawanjana160@gmail.com",
        city="Colombo",
        highest_qualification="Diploma level 4",
        interested_programme="School of Business",
        interested_course="Level 5 Diploma in Business Management",
        source="facebook",
        stage="new_inquiry",
        priority="medium",
        amount=10000.0,
        school="Helena Girls School",
        nationality="Sri Lankan",
    )
    assert payload.full_name == "Nawanjana Amandi"
    assert payload.amount == 10000.0
    assert payload.source == "facebook"


def test_crm_stage_update_schema():
    stage_update = CrmStageUpdate(stage="counselling")
    assert stage_update.stage == "counselling"


def test_crm_csv_export_format():
    # Verify the CSV header columns match the Inspire College Zoho CRM template
    from app.modules.crm.service import export_leads_csv
    import io, csv

    # Test dummy lead object
    class DummyLead:
        lead_id = 123
        assigned_counsellor_id = 5
        counsellor_name = "Sarah D."
        amount = 12000.0
        full_name = "Samal Dimalye"
        followup_date = None
        stage = "enrolled"
        source = "whatsapp"
        created_at = None
        updated_at = None
        notes = "Interested in Psychology"
        message = ""
        social_lead_id = "WA_12345"
        phone = "23052740400"
        whatsapp = "23052740400"
        email = "samaldi.malye@gmail.com"
        school = ""
        nationality = "Mauritius"
        faculty = "School of Psychology"
        interested_course = "Level 4 Diploma in Psychology"
        interested_programme = "School of Psychology"
        highest_qualification = "Diploma level 3"
        student_status = "Payment Done"
        parents_occupation = ""
        parents_email = ""
        address_line1 = ""
        address_line2 = ""
        city = "Port Louis"
        country = "Mauritius"

    # Mock repository
    class MockRepo:
        def __init__(self, db):
            pass
        async def list_export(self, stage, source):
            return [DummyLead()]

    # Verify template export
    import app.modules.crm.service as s
    original_repo = s.CrmLeadRepository
    s.CrmLeadRepository = MockRepo

    import asyncio
    csv_str = asyncio.run(service.export_leads_csv(None, None, None, export_format="template"))
    s.CrmLeadRepository = original_repo

    reader = list(csv.reader(io.StringIO(csv_str)))
    header = reader[0]
    row = reader[1]

    # Verify header contains key Inspire College columns
    assert "Record Id" in header
    assert "Students Pipeline Owner" in header
    assert "Students Pipeline Name" in header
    assert "Faculty  (Schools)" in header
    assert "Selected Program" in header
    assert "Student Status" in header

    # Verify data row values
    assert row[header.index("Record Id")] == "zcrm_123"
    assert row[header.index("Students Pipeline Name")] == "Samal Dimalye"
    assert row[header.index("Stage")] == "Payment Done"
    assert row[header.index("Faculty  (Schools)")] == "School of Psychology"
    assert row[header.index("Nationality")] == "Mauritius"
