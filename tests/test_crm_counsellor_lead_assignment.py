import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import ForbiddenError
from app.modules.crm import router, service
from app.modules.crm.schemas import CrmLeadCreate


def test_counsellor_created_lead_belongs_to_creator_without_rotation():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
        created = SimpleNamespace(lead_id=42, source="manual")
        repo = SimpleNamespace(create=AsyncMock(return_value=created))
        activity_repo = SimpleNamespace(create=AsyncMock())
        payload = CrmLeadCreate(full_name="New Student", phone="0771234567")

        with (
            patch.object(service, "_ensure_awarding_body_column", new_callable=AsyncMock),
            patch.object(service, "_ensure_counsellor_status_table", new_callable=AsyncMock),
            patch.object(service, "CrmLeadRepository", return_value=repo),
            patch.object(service, "CrmActivityRepository", return_value=activity_repo),
            patch.object(service, "get_lead", new_callable=AsyncMock, return_value=created),
            patch.object(service, "apply_auto_assignment", new_callable=AsyncMock) as rotate,
        ):
            await service.create_lead(db, payload, counsellor_id=17, counsellor_name="Counsellor One", assign_to_creator=True)

        saved = repo.create.await_args.args[0]
        assert saved["assigned_counsellor_id"] == 17
        assert saved["counsellor_name"] == "Counsellor One"
        assert saved["assigned_at"] is not None
        rotate.assert_not_awaited()
        db.commit.assert_awaited_once()

    asyncio.run(run())


def test_counsellor_cannot_choose_another_assignee():
    async def run():
        user = SimpleNamespace(user_id=17, email="counsellor@example.com", access=["COUNSELLOR"])
        payload = CrmLeadCreate(full_name="New Student", phone="0771234567", assigned_counsellor_id=18)
        with pytest.raises(ForbiddenError), patch.object(service, "create_lead", new_callable=AsyncMock) as create:
            await router.create_lead(payload, current_user=user, db=SimpleNamespace())
        create.assert_not_awaited()

    asyncio.run(run())


def test_counsellor_create_route_requests_self_assignment():
    async def run():
        user = SimpleNamespace(user_id=17, email="counsellor@example.com", access=["COUNSELLOR"])
        payload = CrmLeadCreate(full_name="New Student", phone="0771234567")
        with (
            patch.object(service, "_actor_name", new_callable=AsyncMock, return_value="Counsellor One"),
            patch.object(service, "create_lead", new_callable=AsyncMock) as create,
        ):
            await router.create_lead(payload, current_user=user, db=SimpleNamespace())
        assert create.await_args.kwargs["assign_to_creator"] is True
        assert create.await_args.kwargs["counsellor_id"] == 17

    asyncio.run(run())


def test_management_create_route_can_choose_assignee():
    async def run():
        user = SimpleNamespace(user_id=1, email="admin@example.com", access=["CRM"])
        payload = CrmLeadCreate(full_name="New Student", phone="0771234567", assigned_counsellor_id=18)
        with (
            patch.object(service, "_actor_name", new_callable=AsyncMock, return_value="CRM Admin"),
            patch.object(service, "create_lead", new_callable=AsyncMock) as create,
        ):
            await router.create_lead(payload, current_user=user, db=SimpleNamespace())
        assert create.await_args.kwargs["assign_to_creator"] is False
        assert create.await_args.args[1].assigned_counsellor_id == 18

    asyncio.run(run())


def test_management_lead_still_uses_rotation_without_explicit_assignee():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
        created = SimpleNamespace(lead_id=43, source="manual")
        repo = SimpleNamespace(create=AsyncMock(return_value=created))
        payload = CrmLeadCreate(full_name="New Student", phone="0771234567")

        with (
            patch.object(service, "_ensure_awarding_body_column", new_callable=AsyncMock),
            patch.object(service, "_ensure_counsellor_status_table", new_callable=AsyncMock),
            patch.object(service, "CrmLeadRepository", return_value=repo),
            patch.object(service, "CrmActivityRepository", return_value=SimpleNamespace(create=AsyncMock())),
            patch.object(service, "get_lead", new_callable=AsyncMock, return_value=created),
            patch.object(service, "apply_auto_assignment", new_callable=AsyncMock) as rotate,
        ):
            await service.create_lead(db, payload, counsellor_id=1, counsellor_name="CRM Admin")

        assert repo.create.await_args.args[0]["assigned_counsellor_id"] is None
        rotate.assert_awaited_once_with(db, 43, commit=False)

    asyncio.run(run())
