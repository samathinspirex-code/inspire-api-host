from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.modules.auth.schemas import CurrentUser
from app.modules.crm import router


@pytest.mark.anyio
async def test_counsellor_report_is_limited_to_signed_in_counsellor(monkeypatch):
    report = object()
    get_report = AsyncMock(return_value=report)
    monkeypatch.setattr(router.service, "get_counsellor_report", get_report)
    user = CurrentUser(user_id=179, email="counsellor@example.com", access=["COUNSELLOR"])

    result = await router.counsellor_report(
        date_from=date(2025, 1, 1),
        date_to=date(2025, 12, 31),
        all_time=False,
        counsellor_id=180,
        programme="HND",
        stage="contactable",
        source="whatsapp",
        db=object(),
        current_user=user,
    )

    assert result is report
    assert get_report.await_args.args[3:] == (179, "HND", False, "contactable", "whatsapp")
