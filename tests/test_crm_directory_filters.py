import asyncio

from app.modules.crm.models.lead import CrmLead
from app.modules.crm.repository import CrmLeadRepository


class _Result:
    def all(self):
        return [(CrmLead(full_name="Example Lead", phone="0771234567", priority="high"), 1)]


class _Session:
    statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Result()


def test_directory_priority_filter_and_sort_are_applied_before_pagination():
    session = _Session()
    leads, total = asyncio.run(CrmLeadRepository(session).list_with_total(
        None, None, None, None, False, 1, 25,
        extra={"priority": "high", "sort": "priority"},
    ))

    sql = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "crm_leads.priority = 'high'" in sql
    assert "ORDER BY CASE" in sql
    assert "LIMIT 25" in sql
    assert total == 1
    assert leads[0].full_name == "Example Lead"


def test_directory_campaign_filter_is_applied():
    session = _Session()
    asyncio.run(CrmLeadRepository(session).list_with_total(
        None, None, None, None, False, 1, 25,
        extra={"campaign": "October Intake"},
    ))

    sql = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "lower(crm_leads.campaign) LIKE lower('October Intake')" in sql


def test_directory_combines_selected_values_within_each_filter():
    session = _Session()
    asyncio.run(CrmLeadRepository(session).list_with_total(
        None, None, None, None, False, 1, 25,
        extra={
            "stages": ["contacted", "not_interested"],
            "sources": ["meta", "whatsapp"],
            "awarding_bodies": ["ATHE", "CPD"],
            "programmes": [f"Programme {number}" for number in range(1, 6)],
        },
    ))
    sql = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "crm_leads.stage IN" in sql
    assert all(value in sql for value in ("contacted", "not_interested", "lost_to_competitor", "cant_afford"))
    assert "crm_leads.source IN" in sql and all(value in sql for value in ("meta", "whatsapp"))
    assert "crm_leads.awarding_body IN" in sql and all(value in sql for value in ("ATHE", "CPD"))
    assert "crm_leads.interested_programme IN" in sql and "crm_leads.interested_course IN" in sql
    assert all(f"Programme {number}" in sql for number in range(1, 6))
