import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.errors import APIError
from app.modules.academic import contact_protection


def test_blocked_contact_ip_rejected_before_captcha_check():
    with patch.object(contact_protection.settings, "CONTACT_RECAPTCHA_ENABLED", False):
        with pytest.raises(APIError) as error:
            asyncio.run(contact_protection.check_contact_submission(None, "185.225.69.203", "127.0.0.1"))
    assert error.value.status_code == 403


def test_contact_requires_captcha_when_enabled():
    with patch.object(contact_protection.settings, "CONTACT_RECAPTCHA_ENABLED", True), \
         patch.object(contact_protection.settings, "CONTACT_RECAPTCHA_SECRET_KEY", "test-secret"):
        with pytest.raises(APIError) as error:
            asyncio.run(contact_protection.check_contact_submission(None, "203.0.113.1", "127.0.0.1"))
    assert error.value.code == "CAPTCHA_REQUIRED"


@pytest.mark.parametrize("hostname,accepted", [("inspirecollege.lk", True), ("another-site.example", False)])
def test_contact_verifies_captcha_with_google(hostname, accepted):
    response = MagicMock()
    response.json.return_value = {"success": True, "hostname": hostname}
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.post.return_value = response
    manager = AsyncMock()
    manager.__aenter__.return_value = client
    with patch.object(contact_protection.settings, "CONTACT_RECAPTCHA_ENABLED", True), \
         patch.object(contact_protection.settings, "CONTACT_RECAPTCHA_SECRET_KEY", "test-secret"), \
         patch.object(contact_protection.httpx, "AsyncClient", return_value=manager):
        if accepted:
            asyncio.run(contact_protection.check_contact_submission("response-token", "203.0.113.1", "127.0.0.1"))
        else:
            with pytest.raises(APIError) as error:
                asyncio.run(contact_protection.check_contact_submission("response-token", "203.0.113.1", "127.0.0.1"))
            assert error.value.code == "CAPTCHA_INVALID"
    sent = client.post.await_args
    assert sent.args[0] == "https://www.google.com/recaptcha/api/siteverify"
    assert sent.kwargs["data"]["remoteip"] == "203.0.113.1"
