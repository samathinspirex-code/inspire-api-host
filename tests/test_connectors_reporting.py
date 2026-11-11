import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine, text

from app.core.errors import ConflictError, ForbiddenError, ValidationError
from app.modules.connectors import providers, registry, service
from app.modules.connectors.router import router
from app.modules.partners.router import require_growth


LMS = {"attendance": 50.0, "records_30d": 2, "dropout": 5.0, "withdrawn": 1, "enrolments": 20, "active_30d": 3, "enrolled_students": 19}


def run(coro):
    return asyncio.run(coro)


def test_account_creation_binds_account_id_and_trims_fields():
    # Execute the actual service SQL against SQLite: catches missing SQL bind names.
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE connectors (connector_id INTEGER PRIMARY KEY, platform TEXT, name TEXT, account_id TEXT, owner TEXT, created_by INTEGER)"))
        db = SimpleNamespace(execute=AsyncMock(side_effect=lambda sql, params: connection.execute(sql, params)), commit=AsyncMock())
        result = run(service.add_account(db, {"platform": "ga4", "name": " Website ", "account_id": " 1234 ", "owner": " Team "}, 9))
        assert result == {"id": 1}
        assert connection.execute(text("SELECT account_id, name, owner FROM connectors")).one() == ("1234", "Website", "Team")
    engine.dispose()


@pytest.mark.parametrize("payload", [{"platform": "unknown", "name": "Name"}, {"platform": "ga4", "name": "  "}])
def test_invalid_accounts_never_write(payload):
    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValidationError):
        run(service.add_account(db, payload, 9))
    db.execute.assert_not_called()


def test_saved_accounts_and_numbers_cannot_claim_to_be_connected():
    account = {"platform": "meta", "status": "connected"}
    account.update(registry.account_state(account, False))
    platforms = registry.platforms_for([account])
    assert len(platforms) == 16
    assert next(p for p in platforms if p["id"] == "meta")["status"] == "Credentials needed"
    assert not any(p["tone"] == "good" for p in platforms)
    assert all(e["status"] == "Unverified" for e in registry.tracking_plan())


def test_readiness_freshness_and_error_are_distinct():
    now = datetime.now(timezone.utc)
    account = {"platform": "ga4"}
    assert registry.account_state(account, False)["status_label"] == "Credentials needed"
    assert registry.account_state(account, True)["can_sync"]
    account["last_synced_at"] = now - timedelta(days=9)
    assert registry.account_state(account, True, now)["stale"]
    account["last_error"] = "Access denied"
    assert registry.account_state(account, True, now)["status_label"] == "Sync failed"


@pytest.mark.parametrize("platform,account_id,body,expected", [
    ("ga4", "1234", {"metricHeaders": [{"name": k} for k in ["activeUsers", "sessions", "screenPageViews", "engagedSessions"]], "rows": [{"metricValues": [{"value": v} for v in ["11", "20", "35", "15"]]}]}, {"activeUsers": 11, "sessions": 20, "screenPageViews": 35, "engagedSessions": 15}),
    ("gsc", "https://example.test/", {"rows": [{"clicks": 5, "impressions": 100, "ctr": .05, "position": 3}]}, {"clicks": 5, "impressions": 100, "ctr": .05, "position": 3}),
    ("yt", "UC" + "a" * 22, {"columnHeaders": [{"name": k} for k in ["views", "estimatedMinutesWatched", "subscribersGained", "subscribersLost"]], "rows": [[100, 50.5, 2, 1]]}, {"views": 100, "estimatedMinutesWatched": 50.5, "subscribersGained": 2, "subscribersLost": 1}),
])
def test_provider_reports_use_refresh_token_and_correct_aggregate_queries(platform, account_id, body, expected):
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            assert b"refresh_token=private-refresh" in request.content
            return httpx.Response(200, json={"access_token": "private-access"})
        assert request.headers["Authorization"] == "Bearer private-access"
        assert "private-access" not in str(request.url)
        if request.url.path.endswith(":batchRunReports"):
            asked = json.loads(request.content)["requests"]
            return httpx.Response(200, json={"reports": [{"metricHeaders": item["metrics"]} for item in asked]})
        if platform == "gsc" and json.loads(request.content).get("dimensions"):
            return httpx.Response(200, json={"rows": [{"keys": ["x"], **body["rows"][0]}]})
        return httpx.Response(200, json=body)
    report = run(providers.fetch_report(platform, account_id, {"refresh_token": "private-refresh"}, "client", "secret", transport=httpx.MockTransport(handle)))
    assert report["metrics"] == expected
    assert report["account_id"] == account_id
    assert "private-" not in json.dumps(report)
    assert (datetime.fromisoformat(report["end"]) - datetime.fromisoformat(report["start"])).days == 29
    if platform == "ga4":
        assert "dimensions" not in json.loads(requests[1].content)  # unique users must not be summed per day
    if platform == "gsc":
        assert "%2F" in str(requests[1].url)
        assert json.loads(requests[1].content)["dataState"] == "final"
    if platform == "yt":
        assert requests[1].url.params["ids"] == "channel==" + account_id


@pytest.mark.parametrize("platform,account", [("ga4", "G-123"), ("gsc", "example.test"), ("yt", "MINE"), ("meta", "act_123")])
def test_invalid_provider_accounts_fail_before_network(platform, account):
    with pytest.raises(ValidationError):
        run(providers.fetch_report(platform, account, {}, "", ""))


def test_provider_denied_access_does_not_expose_body():
    transport = httpx.MockTransport(lambda request: httpx.Response(403, text="private-refresh private-access secret"))
    with pytest.raises(providers.ProviderFailure) as exc:
        run(providers.fetch_report("ga4", "1234", {"refresh_token": "private-refresh"}, "client", "secret", transport=transport))
    assert "private" not in str(exc.value)


def test_failed_sync_preserves_report_and_persists_only_safe_message():
    row = MagicMock()
    row.mappings.return_value.first.return_value = {"connector_id": 1, "platform": "ga4", "account_id": "1234"}
    db = SimpleNamespace(execute=AsyncMock(return_value=row), commit=AsyncMock(), rollback=AsyncMock())
    with patch.object(service, "fetch_report", AsyncMock(side_effect=httpx.ConnectError("private-token"))):
        with pytest.raises(ConflictError) as exc:
            run(service.sync_account(db, 1))
    sql, params = db.execute.await_args.args
    assert "report =" not in str(sql) and "last_synced_at =" not in str(sql)
    assert "private-token" not in json.dumps(params)
    assert "private-token" not in exc.value.message
    db.commit.assert_awaited_once()


def test_credentials_are_account_specific_and_never_returned():
    row = {"connector_id": 1, "platform": "ga4", "name": "Website", "account_id": "1234", "owner": None,
           "status": "awaiting_api", "created_at": datetime.now(timezone.utc), "last_synced_at": None, "last_error": None, "report": None}
    result = MagicMock()
    result.mappings.return_value.all.return_value = [row]
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    bot = AsyncMock(return_value={"receiving": False, "total": 0})
    lms = AsyncMock(return_value={"connected": False, "receiving": False})
    with patch.object(service, "load_whatsapp", bot), patch.object(service, "load_lms", lms), patch.object(service, "capi_status", AsyncMock(return_value={"configured": False})), patch.object(service, "meta_leads_status", AsyncMock(return_value={"configured": False})), patch.object(service, "load_finance", AsyncMock(return_value={"live": False, "mode": "sandbox"})), patch.object(service.settings, "CONNECTORS_CREDENTIALS", {"2": {"refresh_token": "private-refresh"}}), patch.object(service.settings, "CONNECTORS_GOOGLE_CLIENT_ID", "client"), patch.object(service.settings, "CONNECTORS_GOOGLE_CLIENT_SECRET", "private-secret"):
        data = run(service.reporting(db))
    assert not data["accounts"][0]["can_sync"]
    assert "private" not in json.dumps(data)


def test_reporting_and_sync_endpoints_require_cms_admin():
    for route in router.routes:
        assert any(dependency.call is require_growth for dependency in route.dependant.dependencies)
    with pytest.raises(ForbiddenError):
        require_growth(SimpleNamespace(access=["COUNSELLOR"]))
    admin = SimpleNamespace(access=["CMS_ADMIN"])
    assert require_growth(admin) is admin


def test_successful_sync_replaces_only_this_accounts_report_and_clears_error():
    row = MagicMock()
    row.mappings.return_value.first.return_value = {"connector_id": 7, "platform": "ga4", "account_id": "1234"}
    db = SimpleNamespace(execute=AsyncMock(return_value=row), commit=AsyncMock(), rollback=AsyncMock())
    report = {"platform": "ga4", "account_id": "1234", "metrics": {"sessions": 50}}
    with patch.object(service, "fetch_report", AsyncMock(return_value=report)):
        result = run(service.sync_account(db, 7))
    sql, params = db.execute.await_args.args
    assert "last_error = NULL" in str(sql)
    assert "WHERE connector_id = :id" in str(sql)
    assert params["id"] == 7 and json.loads(params["report"]) == report
    assert result["last_synced_at"]
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("body", [[], {"metricHeaders": [], "rows": []}, {"metricHeaders": [{"name": k} for k in ["activeUsers", "sessions", "screenPageViews", "engagedSessions"]], "rows": [{"metricValues": [{"value": "NaN"}]}]}])
def test_malformed_or_non_finite_reports_are_rejected(body):
    def handle(request):
        return httpx.Response(200, json={"access_token": "access"} if request.url.host == "oauth2.googleapis.com" else body)
    with pytest.raises(ValueError):
        run(providers.fetch_report("ga4", "1234", {"refresh_token": "refresh"}, "client", "secret", transport=httpx.MockTransport(handle)))


def test_empty_search_console_report_does_not_invent_position():
    def handle(request):
        return httpx.Response(200, json={"access_token": "access"} if request.url.host == "oauth2.googleapis.com" else {})
    report = run(providers.fetch_report("gsc", "sc-domain:example.test", {"refresh_token": "refresh"}, "client", "secret", transport=httpx.MockTransport(handle)))
    assert report["metrics"] == {"clicks": 0, "impressions": 0}


def test_brain_connected_dataset_reads_real_reports_without_demo_snapshot():
    from app.modules.insights import service as insights
    report = {"metrics": {"sessions": 25}}
    data = {"accounts": [{"platform": "ga4", "report": report, "last_synced_at": "2026-11-02", "status_label": "Stale", "stale": True}]}
    with patch.object(service, "reporting", AsyncMock(return_value=data)), patch.object(insights, "load_snapshot", AsyncMock(side_effect=AssertionError("Must not load demo context"))):
        name, summary = run(insights.dataset_summary(None, "connected", None))
    assert name == "Connected reporting" and '"sessions": 25' in summary
    assert '"stale": true' in summary


def test_copilot_context_includes_connected_reports():
    from app.modules.insights import service as insights
    snap = {"stages": [], "counsellors": [], "students": 1, "total": 2, "awaiting": 1, "in_progress": 1,
            "enrolled": 0, "conversion": 0, "active_month": 2, "top_interest": {"name": "Course", "count": 2},
            "referral_rate": 0, "meta_rate": 0, "peak": "10am", "past_sla": 0, "expected_enrolments": 0}
    goal = {"target": 1000, "start": datetime(2026, 1, 1), "end": datetime(2027, 6, 30)}
    bot = {"total": 0, "month": 0, "new_leads": 0, "merged": 0, "enrolled": 0, "conversion": 0, "unassigned": 0, "waiting": 0, "median_reply_min": None, "reply_sample": 0}
    with patch.object(insights, "load_meta", AsyncMock(return_value=None)), patch.object(insights, "load_finance", AsyncMock(return_value={"live": False, "mode": "sandbox"})), patch.object(insights, "load_lms", AsyncMock(return_value=LMS)), patch.object(insights, "load_whatsapp", AsyncMock(return_value=bot)), patch.object(insights, "load_snapshot", AsyncMock(return_value=snap)), patch.object(insights, "get_mission", AsyncMock(return_value=goal)), patch.object(insights, "dataset_summary", AsyncMock(return_value=("Connected reporting", "Actual sessions: 25; stale"))):
        context = run(insights.context_text(None, None))
    assert "Actual sessions: 25; stale" in context


def test_daily_sync_skips_unconfigured_accounts_and_survives_a_failure():
    accounts = [{"connector_id": 1, "platform": "ga4", "can_sync": True}, {"connector_id": 2, "platform": "gsc", "can_sync": True},
                {"connector_id": 3, "platform": "meta", "can_sync": False}]

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_):
            return False

    sync = AsyncMock(side_effect=[ConflictError("Access denied."), {"id": 2}])
    with patch.object(service, "saved_accounts", AsyncMock(return_value=accounts)), patch.object(service, "sync_account", sync):
        results = run(service.sync_all(Session))
    assert [item["id"] for item in results] == [1, 2]
    assert results[0]["outcome"].startswith("failed") and results[1]["outcome"] == "synced"
    assert sync.await_count == 2


def meta_insights(request):
    params = request.url.params
    action = lambda value: [{"action_type": "lead", "value": str(value)}, {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "9"}]
    if request.url.path.endswith("act_123"):
        return {"name": "Inspire", "account_status": 3, "currency": "LKR"}
    if params.get("level") == "campaign":
        return {"data": [{"campaign_name": "Small", "objective": "OUTCOME_LEADS", "spend": "10", "impressions": "5", "reach": "4", "inline_link_clicks": "1", "actions": action(1)},
                         {"campaign_name": "Big", "objective": "OUTCOME_LEADS", "spend": "90", "impressions": "50", "reach": "40", "inline_link_clicks": "9"}]}
    if params.get("time_increment"):
        # Two pages: the cursor must be followed, and only by cursor.
        if params.get("after") == "page2":
            return {"data": [{"date_start": "2026-10-02", "spend": "60", "inline_link_clicks": "6", "actions": action(1)}]}
        return {"data": [{"date_start": "2026-10-01", "spend": "40", "inline_link_clicks": "4", "actions": action(2)}], "paging": {"cursors": {"after": "page2"}, "next": "https://graph.facebook.com/next?access_token=private-meta"}}
    if params.get("breakdowns") == "age,gender":
        return {"data": [{"age": "18-24", "gender": "male", "spend": "70", "actions": action(2)}, {"age": "18-24", "gender": "unknown", "spend": "30", "actions": action(1)}]}
    if params.get("breakdowns") == "region":
        return {"data": [{"region": "Western Province", "spend": "100", "inline_link_clicks": "10", "actions": [{"action_type": "onsite_conversion.lead_grouped", "value": "3"}]}, {"region": "Unknown", "spend": "0"}]}
    return {"data": [{"spend": "100", "reach": "44", "impressions": "55", "inline_link_clicks": "10", "actions": action(3)}]}


def test_meta_report_keeps_the_token_in_the_header_and_chats_apart_from_leads():
    seen = []
    def handle(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer private-meta"
        assert "private-meta" not in str(request.url)
        return httpx.Response(200, json=meta_insights(request))
    report = run(providers.fetch_report("meta", "123", {"token": "private-meta"}, "", "", transport=httpx.MockTransport(handle)))
    assert all(request.method == "GET" for request in seen)
    assert "private-meta" not in json.dumps(report)
    assert report["metrics"] == {"spend": 100, "reach": 44, "impressions": 55, "link_clicks": 10, "leads": 3, "conversations": 9}
    assert [item["name"] for item in report["breakdowns"]["campaigns"]] == ["Big", "Small"]
    assert [item["date"] for item in report["breakdowns"]["days"]] == ["2026-10-01", "2026-10-02"]
    assert report["breakdowns"]["regions"][0]["leads"] == 3
    assert "unsettled" in report["warnings"][0]


@pytest.mark.parametrize("status,code,expected", [(400, 190, "rejected the token"), (403, 200, "rejected the token"), (400, 17, "rate limit"), (500, 1, "could not supply")])
def test_meta_failures_use_fixed_messages(status, code, expected):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"error": {"code": code, "message": "private-meta leaked"}}))
    with pytest.raises(providers.ProviderFailure) as exc:
        run(providers.fetch_report("meta", "123", {"token": "private-meta"}, "", "", transport=transport))
    assert expected in str(exc.value) and "private" not in str(exc.value)


def test_meta_needs_a_token_before_any_network_call():
    with pytest.raises(ValidationError):
        run(providers.fetch_report("meta", "123", {"token": ""}, "", ""))


def test_meta_token_is_shared_by_every_ad_account():
    account = {"connector_id": 99, "platform": "meta"}
    with patch.object(service.settings, "CONNECTORS_META_TOKEN", "private-meta"):
        assert service._configured(account)
    with patch.object(service.settings, "CONNECTORS_META_TOKEN", ""):
        assert not service._configured(account)
    assert registry.account_state(account, True)["can_sync"]


def mailjet_day(day, sent=90, blocked=5, soft=5):
    return {"Timeslice": f"2026-10-{day:02d}T00:00:00Z", "Total": sent + blocked + soft, "MessageSentCount": sent, "MessageBlockedCount": blocked, "MessageHardBouncedCount": 0, "MessageSoftBouncedCount": soft,
            "MessageQueuedCount": 0, "MessageDeferredCount": 0, "MessageOpenedCount": 0, "MessageClickedCount": 0, "MessageSpamCount": 0, "MessageUnsubscribedCount": 0}


def test_mailjet_report_totals_warnings_and_no_personal_data():
    seen = []

    def handle(request):
        seen.append(request)
        assert request.headers["Authorization"].startswith("Basic ")
        assert "private" not in str(request.url)
        if request.url.path.endswith("/campaign"):
            return httpx.Response(200, json={"Total": 0, "Data": []})
        assert int(request.url.params["Limit"]) >= 31  # Mailjet returns only 10 rows by default.
        return httpx.Response(200, json={"Data": [mailjet_day(2), mailjet_day(1), {**mailjet_day(1), "Timeslice": "2099-01-01T00:00:00Z"}]})  # A partial day must be left out.
    with patch.object(providers, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 15, tzinfo=tz))):
        report = run(providers.fetch_report("email", "mailjet", {"key": "private-key", "secret": "private-secret"}, "", "", transport=httpx.MockTransport(handle)))
    assert report["metrics"]["processed"] == 200 and report["metrics"]["sent"] == 180 and report["metrics"]["blocked"] == 10
    assert [day["date"] for day in report["breakdowns"]["days"]] == ["2026-10-01", "2026-10-02"]
    assert any("blocked" in warning for warning in report["warnings"]) and any("bounced" in warning for warning in report["warnings"])
    assert any("Tracking may be off" in warning for warning in report["warnings"])
    assert "private" not in json.dumps(report)


def test_mailjet_needs_keys_and_never_exposes_them():
    with pytest.raises(ValidationError):
        run(providers.fetch_report("email", "mailjet", {"key": "", "secret": ""}, "", ""))
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="private-secret"))
    with pytest.raises(providers.ProviderFailure) as exc:
        run(providers.fetch_report("email", "mailjet", {"key": "k", "secret": "private-secret"}, "", "", transport=transport))
    assert "private" not in str(exc.value)


def test_mailjet_uses_the_existing_server_keys():
    account = {"connector_id": 9, "platform": "email"}
    with patch.object(service.settings, "MAILJET_API_KEY", "k"), patch.object(service.settings, "MAILJET_SECRET_KEY", "s"):
        assert service._configured(account) and service._credentials(account) == {"key": "k", "secret": "s"}
    with patch.object(service.settings, "MAILJET_API_KEY", ""):
        assert not service._configured(account)


def test_a_weekly_report_is_not_stale_until_it_misses_a_sync():
    now = datetime(2026, 11, 9, tzinfo=timezone.utc)
    account = {"platform": "meta", "last_synced_at": now - timedelta(days=7)}
    assert registry.account_state(account, True, now)["status_label"] == "Receiving data"
    account["last_synced_at"] = now - timedelta(days=9)
    state = registry.account_state(account, True, now)
    assert state["stale"] and state["status_label"] == "Stale"
