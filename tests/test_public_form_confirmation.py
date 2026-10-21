import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.core.public_form_email import FormEmailResult, confirmation_email_content
from app.modules.academic import service


@pytest.mark.parametrize("admission", [True, False])
def test_confirmation_is_personalised_and_html_escaped(admission):
    subject, text, markup = confirmation_email_content("Sam <Student>", admission=admission)
    assert "Inspire College" in subject
    assert "Hi Sam <Student>" in text
    assert "Hi Sam &lt;Student&gt;" in markup
    assert "A counsellor will contact you shortly" in text
    assert "A counsellor will contact you shortly" in markup
    assert ("admission application" in markup) is admission


@pytest.mark.parametrize("admission", [True, False])
def test_confirmation_sends_to_visitor_without_attachments(admission):
    sender = AsyncMock(return_value=FormEmailResult(True))
    with patch.object(service, "send_public_form_email", sender):
        asyncio.run(service._send_visitor_confirmation(
            "Future Student", "student@example.com", admission=admission, reference="test-42",
        ))
    sender.assert_awaited_once()
    message = sender.await_args.kwargs
    assert message["to_email"] == "student@example.com"
    assert message["to_name"] == "Future Student"
    assert message["custom_id"] == "confirmation-test-42"
    assert "attachments" not in message


def test_confirmation_delivery_exception_does_not_fail_saved_submission():
    with patch.object(service, "send_public_form_email", AsyncMock(side_effect=RuntimeError("offline"))):
        asyncio.run(service._send_visitor_confirmation(
            "Future Student", "student@example.com", admission=True, reference="test-42",
        ))
