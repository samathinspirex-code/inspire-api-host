from sqlalchemy import create_engine, select

from app.modules.crm.models.lead import CrmLead
from app.modules.crm.repository import CrmLeadRepository


def test_name_and_sri_lankan_phone_formats_find_the_same_lead():
    engine = create_engine("sqlite:///:memory:")
    CrmLead.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(CrmLead.__table__.insert(), [
            {"full_name": "Amelia Ryan", "phone": "+94 708-123-456", "source": "manual", "stage": "new_lead", "priority": "medium", "is_archived": False},
            {"full_name": "James Kim", "phone": "0771234567", "source": "manual", "stage": "new_lead", "priority": "medium", "is_archived": False},
        ])
        repository = CrmLeadRepository(None)
        for query in ("Amelia", "ryan", "+94708123456", "0708123456", "708123456", "708 123 456", "+94708", "0708", "708"):
            filters = repository._build_filters(None, None, None, query, False)
            names = connection.execute(select(CrmLead.full_name).where(*filters)).scalars().all()
            assert names == ["Amelia Ryan"], query


def test_partial_phone_search_still_respects_counsellor_scope():
    engine = create_engine("sqlite:///:memory:")
    CrmLead.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(CrmLead.__table__.insert(), [
            {"full_name": "Mine", "phone": "0708123456", "assigned_counsellor_id": 7, "source": "manual", "stage": "new_lead", "priority": "medium", "is_archived": False},
            {"full_name": "Another", "phone": "+94708123456", "assigned_counsellor_id": 8, "source": "manual", "stage": "new_lead", "priority": "medium", "is_archived": False},
        ])
        filters = CrmLeadRepository(None)._build_filters(None, None, 7, "708123456", False)
        names = connection.execute(select(CrmLead.full_name).where(*filters)).scalars().all()
        assert names == ["Mine"]
