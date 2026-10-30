"""Thin wrapper over the OpenAI Responses API (plain and streamed)."""

import json
import logging
from collections.abc import AsyncIterator

import httpx

from app.core.config import settings
from app.modules.lms.assistant_service import _download_ssl_context

logger = logging.getLogger(__name__)

RESPONSES_URL = "https://api.openai.com/v1/responses"


def is_configured() -> bool:
    return bool(settings.OPENAI_API_KEY)


def _request(instructions: str, conversation: list[dict], max_output_tokens: int | None, stream: bool) -> dict:
    body = {
        "model": settings.OPENAI_MODEL,
        "instructions": instructions,
        "input": conversation,
        "max_output_tokens": max_output_tokens or settings.OPENAI_MAX_OUTPUT_TOKENS,
        "reasoning": {"effort": "low"},
        "text": {"verbosity": "low"},
        "store": False,
    }
    if stream:
        body["stream"] = True
    return body


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"}


def _output_text(data: dict) -> str:
    texts = [
        content.get("text", "")
        for output in data.get("output", []) if output.get("type") == "message"
        for content in output.get("content", []) if content.get("type") == "output_text"
    ]
    return "\n".join(text.strip() for text in texts if text.strip()).strip()


async def respond(instructions: str, conversation: list[dict], *, max_output_tokens: int | None = None, tag: str = "OpenAI") -> str | None:
    """Return the reply text, or None when the request fails."""
    try:
        async with httpx.AsyncClient(verify=_download_ssl_context(), timeout=settings.OPENAI_TIMEOUT_SECONDS) as client:
            response = await client.post(RESPONSES_URL, headers=_headers(), json=_request(instructions, conversation, max_output_tokens, False))
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("%s request failed with HTTP %s", tag, exc.response.status_code)
        return None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("%s request failed: %s", tag, type(exc).__name__)
        return None
    return _output_text(data) or None


async def stream(instructions: str, conversation: list[dict], *, max_output_tokens: int | None = None, tag: str = "OpenAI") -> AsyncIterator[str]:
    """Yield text deltas. Raises RuntimeError if the request fails before any text arrives."""
    sent = False
    try:
        async with httpx.AsyncClient(verify=_download_ssl_context(), timeout=settings.OPENAI_TIMEOUT_SECONDS) as client:
            async with client.stream("POST", RESPONSES_URL, headers=_headers(), json=_request(instructions, conversation, max_output_tokens, True)) as response:
                if response.status_code >= 400:
                    logger.warning("%s stream failed with HTTP %s", tag, response.status_code)
                    raise RuntimeError("upstream error")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        event = json.loads(payload)
                    except ValueError:
                        continue
                    kind = event.get("type")
                    if kind == "response.output_text.delta" and event.get("delta"):
                        sent = True
                        yield event["delta"]
                    elif kind in ("response.failed", "error"):
                        logger.warning("%s stream reported %s", tag, kind)
                        if not sent:
                            raise RuntimeError("upstream error")
                        return
    except httpx.HTTPError as exc:
        logger.warning("%s stream failed: %s", tag, type(exc).__name__)
        if not sent:
            raise RuntimeError("upstream error") from exc
