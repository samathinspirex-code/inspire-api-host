import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.crm import public_router


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
def test_meta_form_webhook_uses_separate_source(platform):
    async def run():
        payload = public_router.MetaLeadSubmission(
            full_name="Test student", phone="0771234567", platform=platform,
        )
        with patch.object(public_router.service, "create_lead", new_callable=AsyncMock, return_value=SimpleNamespace(lead_id=42)) as create:
            result = await public_router.submit_meta_lead(payload, db=SimpleNamespace())
        assert result["lead_id"] == 42
        assert create.await_args.args[1].source == "meta_lead_forms"
        assert create.await_args.kwargs["counsellor_name"] == f"Meta Lead Forms ({platform})"

    asyncio.run(run())
