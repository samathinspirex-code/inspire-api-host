import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import ConflictError
from app.modules.crm import router, service
from app.modules.crm.schemas import CrmLeadCreate


def test_phone_normalization_matches_sri_lankan_formats():
    formats = ("077 123 4567", "+94 77 123 4567", "94771234567", "0094 77 123 4567", "771234567")
    assert {service._normalized_lead_phone(phone) for phone in formats} == {"94771234567"}
    assert {service._normalized_lead_phone(phone) for phone in ("+94708123456", "0708123456", "708123456")} == {"94708123456"}


def test_duplicate_error_names_existing_lead_and_assigned_counsellor():
    async def run():
        lead = SimpleNamespace(lead_id=42, full_name="Existing student", phone="0771234567", counsellor_name="Samindi", assigned_counsellor_id=8, is_archived=False)
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(lead, "Other name")])))
        with pytest.raises(ConflictError, match=r"Phone \+94 77 123 4567.*lead #42 \(Existing student\).*with phone 0771234567.*assigned to Samindi"):
            await service._reject_duplicate_manual_phone(db, "+94 77 123 4567")

    asyncio.run(run())


def test_same_last_digits_in_a_different_country_is_not_a_duplicate():
    async def run():
        lead = SimpleNamespace(phone="+44 77 123 4567")
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(lead, None)])))
        await service._reject_duplicate_manual_phone(db, "+94 77 123 4567")

    asyncio.run(run())


def test_crm_form_create_enables_duplicate_check():
    async def run():
        user = SimpleNamespace(user_id=7, email="counsellor@example.com", access=["COUNSELLOR"])
        payload = CrmLeadCreate(full_name="New student", phone="0771234567", source="manual", stage="new_lead")
        with (
            patch.object(service, "_actor_name", new_callable=AsyncMock, return_value="Counsellor"),
            patch.object(service, "create_lead", new_callable=AsyncMock) as create,
        ):
            await router.create_lead(payload, current_user=user, db=SimpleNamespace())
        assert create.await_args.kwargs["reject_duplicate_phone"] is True

    asyncio.run(run())
