import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.crm import service
from app.modules.crm.models.lead import CrmLead
from app.modules.crm.schemas import CrmActivityCreate, CrmFollowupComplete, CrmLeadUpdate
from app.core.errors import ValidationError


def test_followup_date_keeps_the_chosen_colombo_day():
    day = (datetime.now(service.COLOMBO) + timedelta(days=2)).date()
    stored = service._scheduled_followup(datetime.fromisoformat(f"{day}T12:00:00+05:30"))
    assert stored.tzinfo is None
    assert service._followup_day(stored) == day


def test_due_today_becomes_overdue_at_six_pm_colombo_time():
    due = service._scheduled_followup(datetime.now(service.COLOMBO) + timedelta(days=1))
    day = service._followup_day(due)
    before = datetime.combine(day, datetime.min.time(), service.COLOMBO).replace(hour=17, minute=59)
    after = before.replace(hour=18)
    assert service._followup_category(due, before) == "today"
    assert service._followup_category(due, after) == "overdue"


def test_completing_followup_logs_outcome_and_does_not_change_sales_stage():
    async def run():
        scheduled = service._scheduled_followup(datetime.now(service.COLOMBO) + timedelta(days=1))
        lead = CrmLead(lead_id=7, full_name="Student", stage="potential", followup_date=scheduled, followup_count=0, unanswered_calls=0)
        db = AsyncMock()
        db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=lead))
        repository = MagicMock()
        repository.create = AsyncMock()
        with patch.object(service, "CrmActivityRepository", return_value=repository), patch.object(service, "get_lead", new_callable=AsyncMock) as get_lead:
            await service.complete_followup(db, 7, CrmFollowupComplete(note="Called and discussed fees", outcome="contacted"), 4, "Counsellor")
        assert lead.stage == "potential"
        assert lead.followup_date is None
        activity = repository.create.await_args.args[0]
        assert activity["activity_type"] == "follow_up_completed"
        assert "Called and discussed fees" in activity["content"]
        assert activity["counsellor_name"] == "Counsellor"
        assert activity["call_outcome"] == "connected"
        db.commit.assert_awaited_once()
        get_lead.assert_awaited_once_with(db, 7)
    asyncio.run(run())


def test_reminder_cannot_be_removed_without_completion():
    async def run():
        lead = CrmLead(lead_id=7, full_name="Student", stage="potential", followup_date=datetime.now(), email="student@example.com")
        repository = MagicMock(get=AsyncMock(return_value=lead))
        with patch.object(service, "CrmLeadRepository", return_value=repository):
            with pytest.raises(ValidationError, match="Complete the follow-up"):
                await service.update_lead(AsyncMock(), 7, CrmLeadUpdate(followup_date=None))
        repository.update.assert_not_called()
    asyncio.run(run())


def test_completion_activity_cannot_be_forged_through_generic_activity_endpoint():
    async def run():
        with pytest.raises(ValidationError, match="Use Complete follow-up"):
            await service.add_activity(AsyncMock(), 7, CrmActivityCreate(activity_type="follow_up_completed", content="Done"))
    asyncio.run(run())
