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
    assert next(p for p in platforms if p["id"] == "meta")["status"] == "Integration pending"
    assert not any(p["tone"] == "good" for p in platforms)
    assert all(e["status"] == "Unverified" for e in registry.tracking_plan())


def test_readiness_freshness_and_error_are_distinct():
    now = datetime.now(timezone.utc)
    account = {"platform": "ga4"}
    assert registry.account_state(account, False)["status_label"] == "Credentials needed"
    assert registry.account_state(account, True)["can_sync"]
    account["last_synced_at"] = now - timedelta(hours=49)
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
    with patch.object(service.settings, "CONNECTORS_CREDENTIALS", {"2": {"refresh_token": "private-refresh"}}), patch.object(service.settings, "CONNECTORS_GOOGLE_CLIENT_ID", "client"), patch.object(service.settings, "CONNECTORS_GOOGLE_CLIENT_SECRET", "private-secret"):
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
    with patch.object(insights, "load_snapshot", AsyncMock(return_value=snap)), patch.object(insights, "get_mission", AsyncMock(return_value=goal)), patch.object(insights, "dataset_summary", AsyncMock(return_value=("Connected reporting", "Actual sessions: 25; stale"))):
        context = run(insights.context_text(None, None))
    assert "Actual sessions: 25; stale" in context
