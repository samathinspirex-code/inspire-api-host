import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.dialects import postgresql

from app.modules.crm.repository import CrmLeadRepository


def test_pipeline_preview_order_matches_updated_lead_list():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
        await CrmLeadRepository(db).pipeline_snapshot(["new_lead"], limit=20)
        statement = db.execute.await_args.args[0]
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "ORDER BY crm_leads.updated_at DESC, crm_leads.lead_id DESC" in sql

    asyncio.run(run())


def test_legacy_outcomes_remain_in_not_interested_pipeline_counts():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
        await CrmLeadRepository(db).pipeline_snapshot(["not_interested"], limit=20)
        count_sql = str(db.execute.await_args_list[0].args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        ))
        preview_sql = str(db.execute.await_args_list[1].args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        ))
        for sql in (count_sql, preview_sql):
            assert "lost_to_competitor" in sql
            assert "cant_afford" in sql
            assert "not_interested" in sql

    asyncio.run(run())
