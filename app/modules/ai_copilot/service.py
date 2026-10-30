"""Admissions copilot: streamed chat plus the short studio prompts."""

import time
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import openai_client
from app.core.config import settings
from app.core.errors import APIError
from app.modules.ai_copilot import prompts
from app.modules.ai_copilot.context_builder import build_context
from app.modules.insights import service as insights
from app.modules.insights.snapshot import lead_journey
from app.modules.partners import service as partners

_requests: dict[int, list[float]] = defaultdict(list)


def _limit(user_id: int) -> None:
    now = time.monotonic()
    window = [stamp for stamp in _requests[user_id] if stamp > now - 3600]
    if len(window) >= settings.COPILOT_RATE_LIMIT_PER_HOUR:
        raise APIError(429, "RATE_LIMITED", "Too many AI requests this hour. Try again later.")
    window.append(now)
    _requests[user_id] = window


def _guard() -> None:
    if not openai_client.is_configured():
        raise APIError(503, "ASSISTANT_UNAVAILABLE", "The copilot is offline until an OpenAI API key is configured.")


async def chat_stream(db: AsyncSession, user_id: int, counsellor_id: int | None, message: str, history: list[dict]):
    _guard()
    _limit(user_id)
    context = await build_context(db, counsellor_id)
    turns = [{"role": item["role"], "content": item["content"]} for item in history[-8:] if item.get("role") in {"user", "assistant"}]
    turns.append({"role": "user", "content": message})
    instructions = f"{prompts.RULES}\n\nDATA:\n{context}"
    async for delta in openai_client.stream(instructions, turns, max_output_tokens=settings.COPILOT_MAX_OUTPUT_TOKENS, tag="Copilot"):
        yield delta


async def _complete(instructions: str, content: str, user_id: int) -> str:
    _guard()
    _limit(user_id)
    reply = await openai_client.respond(instructions, [{"role": "user", "content": content}], max_output_tokens=settings.COPILOT_MAX_OUTPUT_TOKENS, tag="Copilot")
    if not reply:
        raise APIError(502, "ASSISTANT_FAILED", "The copilot could not answer just now. Please try again.")
    return reply


async def analyze(db: AsyncSession, user_id: int, counsellor_id: int | None, dataset: str) -> dict:
    name, summary = await insights.dataset_summary(db, dataset, counsellor_id)
    text = await _complete(prompts.ANALYZE, f"DATASET: {name}\n{summary}", user_id)
    return {"dataset": dataset, "reply": text}


async def brief(db: AsyncSession, user_id: int, counsellor_id: int | None) -> dict:
    text = await _complete(prompts.BRIEF, await insights.context_text(db, counsellor_id), user_id)
    return {"reply": text}


async def draft_message(db: AsyncSession, user_id: int, lead_id: int) -> dict:
    journey = await lead_journey(db, lead_id)
    if not journey:
        raise APIError(404, "NOT_FOUND", "Lead not found")
    content = (
        f"Student: {journey['name']}. Programme: {journey['programme'] or 'not listed'}. Stage: {journey['stage']}. "
        f"Source: {journey['source']}. Score: {journey['score']}. Next step: {journey['next_action']}"
    )
    return {"reply": await _complete(prompts.DRAFT, content, user_id)}


async def caption(db: AsyncSession, user_id: int, link_id: int) -> dict:
    link = await partners.link_for_caption(db, link_id)
    content = (
        f"Write a short {link['platform']} caption (max 60 words, 2–3 relevant hashtags) for {link['name']}, "
        f"a {link['partner_type']} promoting Inspire College"
        f"{(' programme ' + link['programme']) if link['programme'] else ''}. Campaign: {link['campaign']}. "
        f"Friendly Sri Lankan audience. No invented prices or dates. End with the link: {link['url']}"
    )
    return {"reply": await _complete(prompts.DRAFT, content, user_id)}
