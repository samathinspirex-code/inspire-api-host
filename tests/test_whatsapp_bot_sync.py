import asyncio
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.errors import ForbiddenError
from app.modules.whatsapp_bot import leads as wabot_leads, router as wabot_router, supabase, sync

START = datetime(2026, 11, 12, 18, 30, tzinfo=timezone.utc)  # 13 Nov 00:00 Sri Lanka time
CHAMATH = SimpleNamespace(user_id=178, name="Chamath", email="chamath@inspire.college")


def run(coro):
    return asyncio.run(coro)


def bot_lead(lead_id, phone, created, updated=None, **fields):
    return {"id": lead_id, "phone": phone, "created_at": created, "updated_at": updated or created,
            "name": None, "email": None, "qualification": None, "pathway": None, "field": None,
            "course_interest": None, "status": "new", "source": "whatsapp", **fields}


class FakeBot:
    """In-memory, read-only PostgREST supporting the filters sync.py uses. Has no write methods."""

    def __init__(self, leads):
        self.tables = {"leads": leads}

    @staticmethod
    def _match(row, key, cond):
        op, _, arg = cond.partition(".")
        value = row.get(key)
        if op == "gte":
            return value is not None and sync.parse_ts(value) >= sync.parse_ts(arg)
        raise AssertionError(cond)

    async def select(self, table, params=None):
        params = dict(params or {})
        rows = [dict(r) for r in self.tables.get(table, [])]
        clause = params.pop("and", None)
        if clause:
            for part in clause.strip("()").split(","):
                key, _, cond = part.partition(".")
                rows = [r for r in rows if self._match(r, key, cond)]
        rows.sort(key=lambda r: (sync.parse_ts(r["updated_at"]), r["id"]))
        limit = params.get("limit")
        return rows[: int(limit)] if limit else rows


class Crm:
    """Stands in for the CRM database: lead state, cursor, and the mirror call."""

    def __init__(self, cursor=None):
        self.state = {}
        self.meta = {"leads_cursor": cursor} if cursor else {}
        self.mirror = AsyncMock(side_effect=lambda db, row, prev, who, created: {
            "action": "updated" if prev is not None else "created", "lead_id": 1000 + len(self.state),
            "changed": [], "values": wabot_leads.bot_values(row)})

    async def lead_state(self, db, ids):
        return {i: dict(self.state[i]) for i in ids if i in self.state}

    async def save(self, db, bot_id, crm_id, values, stamp):
        self.state[bot_id] = {"crm_lead_id": crm_id, "bot_values": values}

    async def get_meta(self, db, key):
        return self.meta.get(key)

    async def set_meta(self, db, key, value):
        self.meta[key] = value

    def run(self, bot, **kwargs):
        with patch.object(sync, "configured", return_value=True), \
                patch.object(sync, "lead_state", self.lead_state), patch.object(sync, "_save_lead_state", self.save), \
                patch.object(sync, "_meta", self.get_meta), patch.object(sync, "_set_meta", self.set_meta), \
                patch.object(sync, "resolve_assignee", AsyncMock(return_value=CHAMATH)), \
                patch.object(sync, "mirror_lead", self.mirror):
            return run(sync.run_sync(SimpleNamespace(rollback=AsyncMock()), bot=bot, **kwargs))


def leads_since_yesterday():
    return [
        bot_lead("old", "94771111111", "2026-11-10T05:00:00+00:00", "2026-11-13T09:00:00+00:00", name="Before start"),
        bot_lead("a", "94725175179", "2026-11-13T23:07:00+00:00", name="May I know about this program please"),
        bot_lead("b", "94714477392", "2026-11-13T17:08:00+00:00", name="nilani", qualification="After A/L"),
        bot_lead("web", "web_abc123", "2026-11-13T10:00:00+00:00"),
    ]


def test_backfill_from_yesterday_imports_every_phone_lead_once_and_assigns_chamath():
    bot = FakeBot(leads_since_yesterday())
    crm = Crm()
    result = crm.run(bot, since=START)
    assert result["created"] == 2 and result["skipped"] == 1 and not result["errors"]
    assert {c.args[1]["id"] for c in crm.mirror.await_args_list} == {"a", "b"}  # not "old" (created before start), not web
    assert all(c.args[3] is CHAMATH for c in crm.mirror.await_args_list)
    assert crm.meta["leads_cursor"]["since"] == START.isoformat()

    again = crm.run(bot)  # next pass: nothing changed, nothing re-imported
    assert again["created"] == again["updated"] == 0 and crm.mirror.await_count == 2


def test_more_details_from_the_bot_update_the_same_crm_lead():
    bot = FakeBot(leads_since_yesterday())
    crm = Crm()
    crm.run(bot, since=START)
    lead = bot.tables["leads"][1]
    lead.update(name="Kasun Perera", qualification="After O/L", updated_at="2026-11-14T03:00:00+00:00")
    result = crm.run(bot)
    assert result["updated"] == 1 and result["created"] == 0
    call = crm.mirror.await_args
    assert call.args[1]["id"] == "a" and call.args[2]["full_name"] == "May I know about this program please"


def test_first_run_without_a_start_point_imports_nothing_old():
    bot = FakeBot(leads_since_yesterday())
    crm = Crm()
    result = crm.run(bot)
    assert result["created"] == 0 and crm.mirror.await_count == 0
    assert crm.meta["leads_cursor"]["since"]


def test_dry_run_writes_nothing():
    bot = FakeBot(leads_since_yesterday())
    crm = Crm()
    result = crm.run(bot, since=START, dry_run=True)
    assert sorted(r["phone"] for r in result["would_import"]) == ["94714477392", "94725175179"]
    crm.mirror.assert_not_awaited()
    assert crm.state == {} and "leads_cursor" not in crm.meta


def test_failed_lead_is_retried_and_cursor_does_not_skip_it():
    bot = FakeBot(leads_since_yesterday())
    crm = Crm()
    crm.mirror.side_effect = RuntimeError("db down")
    result = crm.run(bot, since=START)
    assert result["errors"] and crm.meta["leads_cursor"]["updated_at"] == START.isoformat()


def test_field_rule_keeps_counsellor_edits_but_follows_bot_changes():
    previous = {"full_name": "Ok", "highest_qualification": "After A/L"}
    current = {"full_name": "Ok", "highest_qualification": "HND (typed by counsellor)", "email": None}
    values = {"full_name": "Thilak", "highest_qualification": "After O/L", "email": "t@x.test"}
    assert wabot_leads.plan_updates(current, values, previous) == {"full_name": "Thilak", "email": "t@x.test"}
    assert wabot_leads.plan_updates({"full_name": "WhatsApp Lead"}, {"full_name": "Nilani"}, None) == {"full_name": "Nilani"}
    assert wabot_leads.plan_updates({"full_name": "Manual name"}, {"full_name": "Bot name"}, None) == {}


def test_bot_values_map_bot_columns_to_crm_columns():
    values = wabot_leads.bot_values(bot_lead("x", "94714477392", "2026-11-13T17:08:00+00:00", name="nilani",
                                             qualification="After A/L", pathway="UK/ATHE", field="Business"))
    assert values["full_name"] == "nilani" and values["highest_qualification"] == "After A/L"
    assert values["awarding_body"] == "ATHE" and values["faculty"] == "Business" and values["whatsapp"] == "94714477392"


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
    crm = Crm()
    with patch.object(supabase.settings, "WABOT_SUPABASE_URL", "https://bot.supabase.test"), \
            patch.object(supabase.settings, "WABOT_SUPABASE_SERVICE_ROLE_KEY", "service-key"), \
            patch.object(sync, "_meta", crm.get_meta), patch.object(sync, "_set_meta", crm.set_meta):
        async def go():
            async with supabase.BotDB(httpx.AsyncClient(transport=httpx.MockTransport(handle))) as bot:
                return await sync.run_sync(SimpleNamespace(rollback=AsyncMock()), bot=bot)
        result = run(go())
    assert "quota" in result["errors"][0] and "service-key" not in str(result)


def test_whatsapp_bot_routes_need_explicit_grant():
    for route in wabot_router.router.routes:
        assert any(d.call is wabot_router.require_whatsapp_bot for d in route.dependant.dependencies)
    with pytest.raises(ForbiddenError):
        run(wabot_router.require_whatsapp_bot(SimpleNamespace(access=["SUPER_ADMIN", "ADMIN", "CMS_ADMIN"])))
    with pytest.raises(ForbiddenError):
        run(wabot_router.require_whatsapp_bot(SimpleNamespace(access=["WHATSAPP_BOT"])))  # old role, removed
    user = SimpleNamespace(access=["CRM"])
    assert run(wabot_router.require_whatsapp_bot(user)) is user
    counsellor = SimpleNamespace(access=["COUNSELLOR"])
    assert run(wabot_router.require_whatsapp_bot(counsellor)) is counsellor


def test_only_crm_users_can_run_an_import():
    sync_route = next(r for r in wabot_router.router.routes if r.path.endswith("/sync") and "POST" in r.methods)
    assert any(d.call is wabot_router.require_whatsapp_bot_manager for d in sync_route.dependant.dependencies)
    with pytest.raises(ForbiddenError):
        run(wabot_router.require_whatsapp_bot_manager(SimpleNamespace(access=["COUNSELLOR"])))
    manager = SimpleNamespace(access=["CRM"])
    assert run(wabot_router.require_whatsapp_bot_manager(manager)) is manager


def test_old_bot_push_routes_are_removed():
    from app.modules.crm import integration_router, public_router
    paths = [r.path for r in integration_router.router.routes] + [r.path for r in public_router.router.routes]
    assert not [p for p in paths if re.search(r"whatsapp", p)]
