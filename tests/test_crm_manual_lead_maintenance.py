import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import ForbiddenError, ValidationError
from app.modules.crm import router, service
from app.modules.crm.schemas import CrmLeadUpdate


def _lead(*, imported=False, source="manual"):
    return SimpleNamespace(
        lead_id=52, source=source, external_record_id="workbook-row-1" if imported else None,
        created_at=datetime(2026, 10, 8, 2, 44), assigned_at=datetime(2026, 10, 8, 2, 44),
        stage="new_lead", email=None, enrollment_email_status=None,
    )


def test_edit_created_date_moves_original_assignment_and_logs_change():
    async def run():
        lead = _lead()
        repo = SimpleNamespace(get=AsyncMock(return_value=lead), update=AsyncMock())
        activity_repo = SimpleNamespace(create=AsyncMock())
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        with (
            patch.object(service, "CrmLeadRepository", return_value=repo),
            patch.object(service, "CrmActivityRepository", return_value=activity_repo),
            patch.object(service, "_validate_lead_state"),
            patch.object(service, "get_lead", new_callable=AsyncMock, return_value=lead),
        ):
            await service.update_lead(db, 52, CrmLeadUpdate(created_at="2025-05-20T09:30"), actor_id=17, actor_name="Counsellor")

        changes = repo.update.await_args.args[1]
        assert changes["created_at"] == datetime(2025, 5, 20, 4, 0)
        assert changes["assigned_at"] == changes["created_at"]
        assert "20 May 2025" in activity_repo.create.await_args.args[0]["content"]
        db.commit.assert_awaited_once()

    asyncio.run(run())


def test_imported_lead_date_can_be_changed_without_shifting_its_local_time():
    async def run():
        lead = _lead(imported=True)
        repo = SimpleNamespace(get=AsyncMock(return_value=lead), update=AsyncMock())
        activity_repo = SimpleNamespace(create=AsyncMock())
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        with (
            patch.object(service, "CrmLeadRepository", return_value=repo),
            patch.object(service, "CrmActivityRepository", return_value=activity_repo),
            patch.object(service, "_validate_lead_state"),
            patch.object(service, "get_lead", new_callable=AsyncMock, return_value=lead),
        ):
            await service.update_lead(db, 52, CrmLeadUpdate(created_at="2025-05-20T09:30"))
        changes = repo.update.await_args.args[1]
        assert changes["created_at"] == datetime(2025, 5, 20, 9, 30)
        assert changes["assigned_at"] == changes["created_at"]
        assert "20 May 2025, 09:30 AM" in activity_repo.create.await_args.args[0]["content"]

    asyncio.run(run())


def test_leads_from_all_sources_can_be_permanently_deleted():
    async def run():
        for lead in (_lead(), _lead(imported=True), _lead(source="website_admission")):
            repo = SimpleNamespace(get=AsyncMock(return_value=lead), delete=AsyncMock())
            with patch.object(service, "CrmLeadRepository", return_value=repo):
                await service.delete_lead(SimpleNamespace(), 52)
            repo.delete.assert_awaited_once_with(lead)

    asyncio.run(run())


def test_counsellor_can_edit_date_and_delete_only_their_own_added_lead():
    async def run():
        user = SimpleNamespace(user_id=17, email="counsellor@example.com", access=["COUNSELLOR"])
        db = SimpleNamespace(scalar=AsyncMock(return_value=17))
        with (
            patch.object(service, "was_manual_lead_created_by", new_callable=AsyncMock, return_value=True),
            patch.object(service, "_actor_name", new_callable=AsyncMock, return_value="Counsellor"),
            patch.object(service, "update_lead", new_callable=AsyncMock) as update,
            patch.object(service, "delete_lead", new_callable=AsyncMock) as delete,
        ):
            await router.update_lead(52, CrmLeadUpdate(created_at="2025-05-20T09:30"), db=db, current_user=user)
            await router.delete_lead(52, db=db, current_user=user)
        update.assert_awaited_once()
        delete.assert_awaited_once_with(db, 52)

        with (
            patch.object(service, "was_manual_lead_created_by", new_callable=AsyncMock, return_value=False),
            patch.object(service, "update_lead", new_callable=AsyncMock) as update,
            patch.object(service, "delete_lead", new_callable=AsyncMock) as delete,
        ):
            with pytest.raises(ForbiddenError):
                await router.update_lead(52, CrmLeadUpdate(created_at="2025-05-20T09:30"), db=db, current_user=user)
            with pytest.raises(ForbiddenError):
                await router.delete_lead(52, db=db, current_user=user)
        update.assert_not_awaited()
        delete.assert_not_awaited()

    asyncio.run(run())


def test_crm_admin_can_edit_date_and_delete_any_source():
    async def run():
        user = SimpleNamespace(user_id=1, email="admin@example.com", access=["CRM"])
        db = SimpleNamespace()
        with (
            patch.object(service, "was_manual_lead_created_by", new_callable=AsyncMock) as creator_check,
            patch.object(service, "_actor_name", new_callable=AsyncMock, return_value="Admin"),
            patch.object(service, "update_lead", new_callable=AsyncMock) as update,
            patch.object(service, "delete_lead", new_callable=AsyncMock) as delete,
        ):
            await router.update_lead(52, CrmLeadUpdate(created_at="2025-05-20T09:30"), db=db, current_user=user)
            await router.delete_lead(52, db=db, current_user=user)
        creator_check.assert_not_awaited()
        update.assert_awaited_once()
        delete.assert_awaited_once_with(db, 52)

    asyncio.run(run())
