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
