import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.errors import ForbiddenError
from app.modules.whatsapp_bot import router as wabot_router, supabase, sync

S0 = "00000000-0000-0000-0000-000000000000"  # earlier chat, same number, never handed over
S1 = "11111111-1111-1111-1111-111111111111"  # pending handover -> import
S2 = "22222222-2222-2222-2222-222222222222"  # old accepted handover -> never import
SW = "33333333-3333-3333-3333-333333333333"  # website chat, pending -> skipped
L1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
L2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def run(coro):
    return asyncio.run(coro)


class FakeBot:
    """In-memory, read-only PostgREST with the filters sync.py uses. Has no write methods."""

    def __init__(self, tables):
        self.tables = tables
        self.calls = []

    def _match(self, row, key, cond):
        value = row.get(key)
        if cond == "not.is.null":
            return value is not None
        op, _, arg = cond.partition(".")
        if op == "eq":
            return str(value) == arg
        if op == "in":
            return str(value) in [a.strip('"') for a in arg.strip("()").split(",")]
        if op == "gt":
            return value is not None and value > arg
        raise AssertionError(cond)

    async def select(self, table, params=None):
        self.calls.append(table)
        params = dict(params or {})
        rows = [dict(r) for r in self.tables.get(table, [])]
        order, limit = params.pop("order", None), params.pop("limit", None)
        params.pop("select", None)
        for key, cond in params.items():
            rows = [r for r in rows if self._match(r, key, cond)]
        if order:
            col, direction = order.split(".")
            rows.sort(key=lambda r: r.get(col) or "", reverse=direction == "desc")
        return rows[: int(limit)] if limit else rows

    async def one(self, table, params):
        rows = await self.select(table, {**params, "limit": "1"})
        return rows[0] if rows else None


def tables():
    return {
        "leads": [
            {"id": L1, "phone": "94755587843", "name": "Sinhalen dannako", "email": None, "qualification": "After A/L",
             "pathway": "UK/ATHE", "field": "Business", "course_interest": None, "status": "new", "notes": None,
             "created_at": "2026-11-13T05:00:00.12+00:00"},
            {"id": L2, "phone": "94710000000", "name": "Old student", "status": "new", "created_at": "2026-10-01T05:00:00+00:00"},
        ],
        "chat_sessions": [
            {"id": S0, "phone": "94755587843", "lead_id": L1, "source": "whatsapp", "status": "closed"},
            {"id": S1, "phone": "94755587843", "lead_id": L1, "source": "whatsapp", "status": "active"},
            {"id": S2, "phone": "94710000000", "lead_id": L2, "source": "whatsapp", "status": "handed_over", "assigned_counselor_id": "c1"},
            {"id": SW, "phone": "web_abc", "lead_id": None, "source": "website", "status": "active"},
        ],
        "handovers": [
            {"id": "h1", "chat_session_id": S1, "lead_id": L1, "status": "pending", "counselor_id": None,
             "reason": "Qualification flow completed", "assigned_at": "2026-11-13T05:42:00+00:00"},
            {"id": "h2", "chat_session_id": S2, "lead_id": L2, "status": "accepted", "counselor_id": "c1",
             "reason": "Qualification flow completed", "assigned_at": "2026-10-01T06:00:00+00:00"},
            {"id": "hw", "chat_session_id": SW, "lead_id": None, "status": "pending", "counselor_id": None,
             "reason": "x", "assigned_at": "2026-11-13T06:00:00+00:00"},
        ],
        "chat_messages": [
            {"session_id": S0, "sender": "user", "message": "earlier hi", "created_at": "2026-11-12T08:00:00+00:00"},
            {"session_id": S1, "sender": "user", "message": "hello", "created_at": "2026-11-13T05:30:00+00:00"},
            {"session_id": S1, "sender": "bot", "message": "hi there", "created_at": "2026-11-13T05:31:00+00:00"},
        ],
    }


class Harness:
    """Stands in for the CRM database: import records plus mocked CRM writes."""

    def __init__(self):
        self.store = {}
        self.lead_sync = AsyncMock(return_value={"status": "success", "action": "created", "lead_id": 42, "counsellor_matched": True})
        self.message_sync = AsyncMock(return_value={"status": "success", "lead_id": 42})

    async def imported(self, db, session_ids=None):
        ids = self.store.keys() if session_ids is None else session_ids
        return {i: dict(self.store[i]) for i in ids if i in self.store}

    async def save(self, db, sid, hid, lid, last):
        old = self.store.get(sid, {})
        self.store[sid] = {"bot_handover_id": old.get("bot_handover_id") or hid, "crm_lead_id": lid or old.get("crm_lead_id"),
                           "last_message_at": max([t for t in (last, old.get("last_message_at")) if t], default=None),
                           "handover_synced_at": old.get("handover_synced_at") or last}

    def run(self, bot, picked=None, **kwargs):
        with patch.object(sync, "configured", return_value=True), \
                patch.object(sync, "pick_for_group", AsyncMock(return_value=picked)), \
                patch.object(sync, "imported", self.imported), patch.object(sync, "_save_state", self.save), \
                patch.object(sync, "_save_run", AsyncMock()), \
                patch.object(sync.settings, "WABOT_ASSIGN_TO_EMAIL", "samindi@inspire.college"), \
                patch.object(sync, "sync_whatsapp_bot_lead", self.lead_sync), \
                patch.object(sync, "add_whatsapp_bot_message", self.message_sync):
            return run(sync.run_sync(SimpleNamespace(rollback=AsyncMock()), bot=bot, **kwargs))


def test_only_pending_handovers_are_imported_and_assigned_to_samindi():
    bot = FakeBot(tables())
    h = Harness()
    result = h.run(bot)
    assert result["imported"] == 1 and result["skipped"] == 1 and not result["errors"]
    payload = h.lead_sync.await_args.args[1]
    assert payload.bot_session_id == S1 and payload.full_name == "Sinhalen dannako"
    assert payload.counsellor.email == "samindi@inspire.college"
    assert payload.handover_reason == "Qualification flow completed"
    assert [m.message for m in payload.transcript] == ["earlier hi", "hello", "hi there"]
    assert S2 not in h.store  # the old accepted handover is not backfilled


def test_counsellors_chosen_in_crm_override_the_env_fallback():
    bot = FakeBot(tables())
    h = Harness()
    h.run(bot, picked=SimpleNamespace(user_id=9, name="Kamala", email="kamala@inspire.college"))
    assert h.lead_sync.await_args.args[1].counsellor.email == "kamala@inspire.college"


def test_dry_run_does_not_move_the_turn_pointer():
    bot = FakeBot(tables())
    h = Harness()
    pick = AsyncMock(return_value=None)
    with patch.object(sync, "pick_for_group", pick), patch.object(sync, "configured", return_value=True), \
            patch.object(sync, "imported", h.imported), patch.object(sync.settings, "WABOT_ASSIGN_TO_EMAIL", "samindi@inspire.college"):
        result = run(sync.run_sync(SimpleNamespace(rollback=AsyncMock()), bot=bot, dry_run=True))
    assert result["would_import"][0]["assign_to"] == "samindi@inspire.college"
    assert pick.await_args.kwargs["commit_turn"] is False


def test_second_run_does_not_reimport_and_copies_only_new_messages():
    bot = FakeBot(tables())
    h = Harness()
    h.run(bot)
    second = h.run(bot)
    assert second["imported"] == 0 and second["already_imported"] == 1 and h.lead_sync.await_count == 1
    h.message_sync.assert_not_awaited()

    bot.tables["chat_messages"].append({"session_id": S1, "sender": "user", "message": "When can I start?", "created_at": "2026-11-13T07:00:00+00:00"})
    # Someone accepts the handover in the bot later: the chat is still followed.
    bot.tables["handovers"][0]["status"] = "accepted"
    third = h.run(bot)
    assert third["messages"] == 1 and h.lead_sync.await_count == 1
    h.run(bot)
    assert h.message_sync.await_count == 1


def test_dry_run_writes_nothing():
    bot = FakeBot(tables())
    h = Harness()
    result = h.run(bot, dry_run=True)
    assert [r["phone"] for r in result["would_import"]] == ["94755587843"]
    h.lead_sync.assert_not_awaited()
    assert h.store == {}


def test_bot_client_refuses_every_write():
    assert not any(hasattr(supabase.BotDB, name) for name in ("insert", "update", "delete", "upsert"))
    seen = []

    def handle(request):
        seen.append(request.method)
        return httpx.Response(200, json=[])

    with patch.object(supabase.settings, "WABOT_SUPABASE_URL", "https://bot.supabase.test"), \
            patch.object(supabase.settings, "WABOT_SUPABASE_SERVICE_ROLE_KEY", "service-key"):
        async def go():
            async with supabase.BotDB(httpx.AsyncClient(transport=httpx.MockTransport(handle))) as bot:
                await bot.select("leads")
                with pytest.raises(RuntimeError, match="read-only"):
                    await bot._request("PATCH", "leads")
                with pytest.raises(RuntimeError, match="read-only"):
                    await bot._request("POST", "leads")
        run(go())
    assert seen == ["GET"]


def test_bot_router_has_no_write_routes_except_crm_import():
    writes = [(r.path, m) for r in wabot_router.router.routes for m in r.methods if m not in {"GET", "HEAD"}]
    assert writes == [("/api/v1/cms/whatsapp-bot/sync", "POST")]


def test_unreachable_bot_database_is_reported_not_raised():
    def handle(request):
        return httpx.Response(402, json={"message": "exceed_egress_quota"})
    with patch.object(supabase.settings, "WABOT_SUPABASE_URL", "https://bot.supabase.test"), \
            patch.object(supabase.settings, "WABOT_SUPABASE_SERVICE_ROLE_KEY", "service-key"):
        async def go():
            async with supabase.BotDB(httpx.AsyncClient(transport=httpx.MockTransport(handle))) as bot:
                with patch.object(sync, "_save_run", AsyncMock()):
                    return await sync.run_sync(SimpleNamespace(rollback=AsyncMock()), bot=bot)
        result = run(go())
    assert "quota" in result["errors"][0] and "service-key" not in str(result)


def test_whatsapp_bot_routes_need_explicit_grant():
    for route in wabot_router.router.routes:
        assert any(d.call is wabot_router.require_whatsapp_bot for d in route.dependant.dependencies)
    with pytest.raises(ForbiddenError):
        run(wabot_router.require_whatsapp_bot(SimpleNamespace(access=["SUPER_ADMIN", "ADMIN", "CRM"])))
    user = SimpleNamespace(access=["WHATSAPP_BOT"])
    assert run(wabot_router.require_whatsapp_bot(user)) is user
    counsellor = SimpleNamespace(access=["COUNSELLOR"])
    assert run(wabot_router.require_whatsapp_bot(counsellor)) is counsellor


def test_only_whatsapp_bot_role_can_run_an_import():
    sync_route = next(r for r in wabot_router.router.routes if r.path.endswith("/sync") and "POST" in r.methods)
    assert any(d.call is wabot_router.require_whatsapp_bot_manager for d in sync_route.dependant.dependencies)
    with pytest.raises(ForbiddenError):
        run(wabot_router.require_whatsapp_bot_manager(SimpleNamespace(access=["COUNSELLOR"])))
    manager = SimpleNamespace(access=["WHATSAPP_BOT"])
    assert run(wabot_router.require_whatsapp_bot_manager(manager)) is manager


def test_old_bot_push_routes_are_removed():
    from app.modules.crm import integration_router, public_router
    paths = [r.path for r in integration_router.router.routes] + [r.path for r in public_router.router.routes]
    assert not [p for p in paths if re.search(r"whatsapp", p)]
