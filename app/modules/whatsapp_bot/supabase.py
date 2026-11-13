"""Server-side client for the WhatsApp bot's own Supabase (PostgREST).

The bot (bot/insbot-1) keeps its leads, chats, counsellors and handovers in a
separate Supabase project. The CRM only READS them here, with the service-role
key, which never leaves the API. Writes are refused by design: the bot system
stays untouched and its data is copied into the CRM instead.
"""

from typing import Optional

import httpx
from fastapi import status

from app.core.config import settings
from app.core.errors import APIError

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
READ_METHODS = {"GET", "HEAD"}


class BotUnavailable(APIError):
    def __init__(self, message: str):
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, "WABOT_UNAVAILABLE", message)


def configured() -> bool:
    return bool(settings.WABOT_SUPABASE_URL and settings.WABOT_SUPABASE_SERVICE_ROLE_KEY)


def _base() -> str:
    if not configured():
        raise BotUnavailable("WhatsApp bot database is not configured. Set WABOT_SUPABASE_URL and WABOT_SUPABASE_SERVICE_ROLE_KEY on the API.")
    return settings.WABOT_SUPABASE_URL.rstrip("/") + "/rest/v1"


def _headers(extra: Optional[dict] = None) -> dict:
    key = settings.WABOT_SUPABASE_SERVICE_ROLE_KEY
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}
    if extra:
        headers.update(extra)
    return headers


def _describe(response: httpx.Response) -> str:
    try:
        body = response.json()
        detail = body.get("message") or body.get("error") or body.get("hint") or str(body)
    except ValueError:
        detail = response.text[:300]
    if "egress" in str(detail).lower() or response.status_code == 402:
        return "WhatsApp bot database has exceeded its Supabase quota. Upgrade or wait for the quota to reset."
    return f"WhatsApp bot database error ({response.status_code}): {detail}"


def in_list(values) -> str:
    """PostgREST `in.(...)` filter value with quoted items."""
    quoted = ",".join('"' + str(v).replace('"', "") + '"' for v in values)
    return f"in.({quoted})"


class BotDB:
    """Thin async PostgREST wrapper. Use as `async with BotDB() as bot:`."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client
        self._owned = client is None

    async def __aenter__(self) -> "BotDB":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=TIMEOUT)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()

    async def _request(self, method: str, table: str, params: Optional[dict] = None,
                       headers: Optional[dict] = None) -> httpx.Response:
        if method not in READ_METHODS:
            raise RuntimeError("The WhatsApp bot database is read-only from the CRM")
        url = f"{_base()}/{table}"
        try:
            response = await self._client.request(method, url, params=params, headers=_headers(headers))
        except httpx.HTTPError as exc:
            raise BotUnavailable(f"WhatsApp bot database is unreachable: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            raise BotUnavailable(_describe(response))
        return response

    async def select(self, table: str, params: Optional[dict] = None) -> list[dict]:
        params = {"select": "*", **(params or {})}
        return (await self._request("GET", table, params=params)).json()

    async def one(self, table: str, params: dict) -> Optional[dict]:
        rows = await self.select(table, {**params, "limit": "1"})
        return rows[0] if rows else None

    async def count(self, table: str, params: Optional[dict] = None) -> int:
        response = await self._request(
            "HEAD", table, params={"select": "id", **(params or {})}, headers={"Prefer": "count=exact"}
        )
        content_range = response.headers.get("content-range", "*/0")
        total = content_range.split("/")[-1]
        return int(total) if total.isdigit() else 0
