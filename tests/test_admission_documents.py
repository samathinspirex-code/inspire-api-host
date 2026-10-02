import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core import public_form_email
from app.core.mailjet_smtp import _build_email
from app.modules.academic import service


def test_smtp_fallback_includes_admission_document():
    contents = b"%PDF-1.4\napplication results"
    message = _build_email({
        "From": {"Email": "admissions@example.com"},
        "To": [{"Email": "staff@example.com"}],
        "Subject": "New application",
        "TextPart": "New application",
        "HTMLPart": "<p>New application</p>",
        "Attachments": [{
            "ContentType": "application/pdf",
            "Filename": "results.pdf",
            "Base64Content": base64.b64encode(contents).decode(),
        }],
    })
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "results.pdf"
    assert attachments[0].get_payload(decode=True) == contents


def test_mailjet_api_receives_admission_document():
    async def run():
        client = MagicMock()
        response = MagicMock(is_error=False)
        response.json.return_value = {"Messages": [{"Status": "success"}]}
        client.post = AsyncMock(return_value=response)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=None)
        attachment = {"ContentType": "application/pdf", "Filename": "results.pdf", "Base64Content": "JVBERg=="}
        with (
            patch.object(public_form_email.settings, "MAILJET_API_KEY", "key"),
            patch.object(public_form_email.settings, "MAILJET_SECRET_KEY", "secret"),
            patch.object(public_form_email.settings, "PUBLIC_FORM_FROM_EMAIL", "from@example.com"),
            patch.object(public_form_email.settings, "PUBLIC_FORM_RECIPIENT_EMAIL", "staff@example.com"),
            patch.object(public_form_email.httpx, "AsyncClient", return_value=context),
        ):
            result = await public_form_email.send_public_form_email("Application", "Text", "<p>Text</p>", attachments=[attachment])
        assert result.sent
        assert client.post.await_args.kwargs["json"]["Messages"][0]["Attachments"] == [attachment]
    asyncio.run(run())


def test_previous_admission_document_gets_private_short_lived_link():
    async def run():
        row = {
            "result_document_key": "admissions/results/old-id/result.pdf",
            "result_document_name": "results.pdf",
            "result_document_content_type": "application/pdf",
        }
        result = MagicMock()
        result.mappings.return_value.first.return_value = row
        db = SimpleNamespace(execute=AsyncMock(return_value=result))
        client = MagicMock()
        client.generate_presigned_url.return_value = "https://private.example/signed"
        with (
            patch.object(service.media_service, "_client", return_value=client),
            patch.object(service.settings, "MEDIA_BUCKET", "private-bucket"),
        ):
            document = await service.get_crm_admission_document(db, 42)
        assert document == {"name": "results.pdf", "url": "https://private.example/signed"}
        assert db.execute.await_args.args[1] == {"lead_id": 42}
        assert client.generate_presigned_url.call_args.kwargs["ExpiresIn"] == 300
    asyncio.run(run())
