import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.errors import ValidationError
from app.modules.connectors import providers, registry
from app.modules.insights import service as insights, studio_data
from app.modules.insights.website import ga4_events, latest_report, visitors_view
from app.modules.insights.whatsapp import context_line, summarize


NOW = datetime(2026, 11, 3, 12, 0)


def run(coro):
    return asyncio.run(coro)


def lead(lead_id=1, *, hours=2, reply_minutes=None, stage="new_lead", counsellor=7, external="wabot:a", source="whatsapp"):
    handed = NOW - timedelta(hours=hours)
    return {"lead_id": lead_id, "full_name": f"Lead {lead_id}", "stage": stage, "source": source, "external_record_id": external,
            "assigned_counsellor_id": counsellor, "counsellor_name": "Nimal" if counsellor else None, "programme": "HND",
            "handed_at": handed, "first_reply_at": handed + timedelta(minutes=reply_minutes) if reply_minutes is not None else None}


def test_bot_summary_counts_are_measured_not_assumed():
    rows = [
        lead(1, reply_minutes=10),
        lead(2, hours=30),  # no reply, past 24h
        lead(3, reply_minutes=30, stage="enrolled", counsellor=None),
        lead(4, hours=24 * 40, external="meta:9", source="meta_lead_forms", reply_minutes=5),  # merged, outside 30 days
    ]
    bot = summarize(rows, {"student": 3, "counsellor": 1}, NOW)
    assert (bot["total"], bot["month"], bot["new_leads"], bot["merged"]) == (4, 3, 3, 1)
    assert (bot["enrolled"], bot["conversion"], bot["unassigned"]) == (1, 25.0, 1)
    assert (bot["waiting"], bot["waiting_over_day"]) == (1, 1)
    assert (bot["median_reply_min"], bot["reply_sample"]) == (20, 2)  # the 40-day-old reply is outside the window
    assert bot["receiving"] is True
    assert sum(point["count"] for point in bot["daily"]) == 3
    assert {person["name"] for person in bot["counsellors"]} == {"Nimal", "Unassigned"}


def test_empty_bot_summary_invents_nothing():
    bot = summarize([], {"student": 0, "counsellor": 0}, NOW)
    assert bot["median_reply_min"] is None and bot["last_handover_at"] is None and bot["receiving"] is False
    assert "no logged replies yet" in context_line(bot)


def test_whatsapp_platform_status_comes_from_handovers():
    def status(bot):
        return next(p for p in registry.platforms_for([], bot) if p["id"] == "wa")["status"]
    assert status(summarize([lead()], {}, NOW)) == "Receiving handovers"
    assert status(summarize([lead(hours=24 * 20)], {}, NOW)) == "No recent handovers"
    assert status(summarize([], {}, NOW)) == "Waiting for first handover"


def test_whatsapp_channel_row_is_live_and_other_rows_stay_demo():
    rows = insights._channels(summarize([lead(reply_minutes=5)], {}, NOW))
    row = next(item for item in rows if item["name"] == "WhatsApp Business")
    assert row["live"] and row["leads"] == 1 and row["spend"] == 0 and sum(row["trend"]) == 1
    assert next(item for item in rows if item["name"] == "Meta Ads")["source"] == "demo"


def ga4_account(**report):
    return {"platform": "ga4", "name": "Website", "last_synced_at": datetime.now(timezone.utc).isoformat(), "report": {
        "start": "2026-10-04", "end": "2026-11-02", "reporting_note": "note",
        "metrics": {"activeUsers": 80, "sessions": 100, "screenPageViews": 300, "engagedSessions": 50}, **report}}


BREAKDOWNS = {
    "pages": [{"path": "/programs/hnd", "views": 120, "users": 40, "engagement_seconds": 1800}],
    "countries": [{"name": "Sri Lanka", "sessions": 75}, {"name": "Qatar", "sessions": 25}, {"name": "(not set)", "sessions": 10}],
    "devices": [{"name": "mobile", "sessions": 60, "engagement_seconds": 600}, {"name": "desktop", "sessions": 40, "engagement_seconds": 400}],
    "channels": [{"name": "Paid Social", "sessions": 70}],
    "events": [{"name": "generate_lead", "count": 4}, {"name": "whatsapp_click", "count": 9}],
}


def test_visitors_view_uses_additive_sessions_and_ignores_unknown_countries():
    view = visitors_view(ga4_account(breakdowns=BREAKDOWNS))
    assert view["live"] and view["fresh"]
    assert (view["overseas"], view["mobile"], view["session_to_lead"]) == (25.0, 60.0, 4.0)
    assert view["pages"][0] == {"path": "/programs/hnd", "views": 120, "users": 40, "time": "0:15", "student": 60, "parent": 16}
    assert view["avg_engagement"] == "0:10"


def test_older_reports_without_breakdowns_still_render_totals():
    view = visitors_view(ga4_account())
    assert view["sessions"] == 100 and view["has_breakdowns"] is False and view["pages"] == []
    assert ga4_events([ga4_account()]) is None


def test_no_ga4_report_means_no_live_view():
    assert visitors_view(latest_report([{"platform": "ga4", "report": None, "last_synced_at": None}], "ga4")) is None


def test_tracking_plan_reports_only_ga4_evidence():
    assert all(event["status"] == "Unverified" for event in registry.tracking_plan(None))
    plan = {event["event"]: event for event in registry.tracking_plan(ga4_events([ga4_account(breakdowns=BREAKDOWNS)]))}
    assert plan["whatsapp_click"]["status"] == "Receiving" and plan["whatsapp_click"]["count"] == 9
    assert plan["brochure_download"]["status"] == "Not seen in GA4"


def test_ga4_breakdowns_are_saved_from_batches_within_the_api_limit():
    batches = []

    def handle(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "token"})
        if request.url.path.endswith(":batchRunReports"):
            asked = json.loads(request.content)["requests"]
            batches.append(asked)
            return httpx.Response(200, json={"reports": [
                {"metricHeaders": item["metrics"],
                 "rows": [{"dimensionValues": [{"value": "x"} for _ in item["dimensions"]], "metricValues": [{"value": "5"} for _ in item["metrics"]]}]}
                for item in asked]})
        return httpx.Response(200, json={"metricHeaders": [{"name": k} for k in ["activeUsers", "sessions", "screenPageViews", "engagedSessions"]],
                                         "rows": [{"metricValues": [{"value": "1"}] * 4}]})

    report = run(providers.fetch_report("ga4", "1234", {"refresh_token": "r"}, "c", "s", transport=httpx.MockTransport(handle)))
    assert all(len(batch) <= 5 for batch in batches) and sum(len(batch) for batch in batches) == len(providers.GA4_BREAKDOWNS)
    dimensions = [d["name"] for batch in batches for item in batch for d in item["dimensions"]]
    assert "pagePathPlusQueryString" not in dimensions  # query strings can carry personal data
    assert report["breakdowns"]["pages"] == [{"path": "x", "views": 5, "users": 5, "engagement_seconds": 5}]
    assert report["breakdowns"]["cities"] == [{"city": "x", "country": "x", "users": 5}]


def test_realtime_reports_totals_only():
    def handle(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(200, json={"rows": [{"dimensionValues": [{"value": "mobile"}], "metricValues": [{"value": "3"}]}]})
    result = run(providers.ga4_realtime("1234", {"refresh_token": "r"}, "c", "s", transport=httpx.MockTransport(handle)))
    assert result["available"] and result["active"] == 3 and result["pages"] == [{"name": "mobile", "value": 3}]


def test_source_flow_uses_real_stage_counts_and_folds_small_sources():
    rows = [("meta_lead_forms", "enrolled", 5), ("meta_lead_forms", "uncontactable", 10), ("whatsapp", "contacted", 4)] + [(f"s{i}", "new_lead", 1) for i in range(8)]
    flow = studio_data.build_flow(rows)
    assert flow["total"] == 27 and len(flow["sources"]) == 7
    assert sum(s["total"] for s in flow["sources"]) == 27
    assert sum(o["total"] for o in flow["outcomes"]) == 27
    assert next(o for o in flow["outcomes"] if o["name"] == "Enrolled")["total"] == 5
    assert any(s["name"] == "Other" for s in flow["sources"])


def test_phone_numbers_are_masked_on_the_channels_table():
    assert studio_data.mask_number("+94 77 123 4567") == "+94 77 *** 4567"
    assert studio_data.mask_number(None) == "—"


CONTAINER = '{"function":"__googtag","vtp_tagId":"G-AAA111"},{"function":"__googtag","once_per_event":true,"vtp_tagId":"G-AAA111"}'


def gtm_transport(container=200, html='<script>GTM-TEST123</script>'):
    def handle(request):
        if request.url.host == "www.googletagmanager.com":
            return httpx.Response(container, text=CONTAINER)
        return httpx.Response(200, text=html)
    return httpx.MockTransport(handle)


def test_gtm_check_needs_no_credentials_and_warns_about_duplicate_ga4():
    report = run(providers.fetch_report("gtm", "GTM-TEST123", {}, "", "", transport=gtm_transport(html='GTM-TEST123 gtag/js?id=G-AAA111')))
    assert report["checks"]["installed_on_site"] is True
    assert report["metrics"] == {"ga4_tags": 2}
    assert any("hard-coded" in item for item in report["checks"]["warnings"])
    assert any("2 Google tags" in item for item in report["checks"]["warnings"])


def test_gtm_check_flags_a_container_missing_from_the_site():
    report = run(providers.fetch_report("gtm", "GTM-TEST123", {}, "", "", transport=gtm_transport(html="<html></html>")))
    assert report["checks"]["installed_on_site"] is False
    state = registry.account_state({"platform": "gtm", "report": report, "last_synced_at": datetime.now(timezone.utc)}, True)
    assert (state["status_label"], state["tone"]) == ("Check tags", "warn")


def test_gtm_rejects_bad_ids_and_unpublished_containers():
    with pytest.raises(ValidationError):
        run(providers.fetch_report("gtm", "G-123", {}, "", ""))
    with pytest.raises(providers.ProviderFailure):
        run(providers.fetch_report("gtm", "GTM-TEST123", {}, "", "", transport=gtm_transport(container=404)))


def test_whatsapp_dataset_summary_is_live():
    bot = summarize([lead()], {"student": 0, "counsellor": 0}, NOW)
    with patch.object(insights, "load_whatsapp", AsyncMock(return_value=bot)):
        name, summary = run(insights.dataset_summary(None, "whatsapp", None))
    assert name == "WhatsApp bot" and "1 handed over" in summary


SNAP = {
    "generated_at": "2026-11-03T00:00:00", "programs": 5, "students": 100, "total": 40, "awaiting": 10, "in_progress": 12, "active_month": 20,
    "published_by_type": {"video": 2, "pdf": 1}, "published": 4, "draft": 1, "conversion": 5.0, "enrolled": 2, "contacted": 8, "uncontactable": 3,
    "activity": [], "top_interest": {"name": "HND", "count": 9}, "stages": [{"key": "new_lead", "label": "New Lead", "count": 10}], "stage_map": {"follow_up": 4},
    "month_enrolled": 2, "past_sla": 1, "aging": [{"key": "7d+", "count": 2}], "daily": [{"date": "2026-11-01", "count": 1}], "median_reply_min": 20,
    "response_buckets": [], "response_source": "modelled", "velocity": [], "weekday": [], "counsellors": [], "expected_enrolments": 1.5, "sources": [],
    "programmes": [], "heatmap": [], "heatmap_source": "cms", "peak": "10am", "referral_rate": 4, "meta_rate": 1, "hot": 1, "warm": 2, "open_leads": 30,
}
GOAL = {"target": 1000, "start": datetime(2026, 1, 1).date(), "end": datetime(2027, 6, 30).date()}


def pages(site):
    bot = summarize([lead(1, reply_minutes=10), lead(2, counsellor=None)], {"student": 1, "counsellor": 1}, datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3))
    crm = {"source_flow": {"total": 0, "outcomes": [], "sources": []}, "source_programme": {"programmes": [], "rows": []},
           "counsellor_source": {"sources": [], "rows": []}, "whatsapp_numbers": {7: "+94 77 123 4567"}, "tracked_people": [],
           "referral_summary": {"funnel": {"leads": 0, "contacted": 0, "counselled": 0, "enrolled": 0}, "codes": [], "invites": []}}
    patches = [patch.object(studio_data, name, AsyncMock(return_value=value)) for name, value in crm.items()]
    for item in patches:
        item.start()
    try:
        with patch.object(insights, "load_snapshot", AsyncMock(return_value=SNAP)), patch.object(insights, "get_mission", AsyncMock(return_value=GOAL)), \
                patch.object(insights, "load_whatsapp", AsyncMock(return_value=bot)), patch.object(insights, "_website", AsyncMock(return_value=site)):
            return {
                "dashboard": run(insights.dashboard(None, None)), "command": run(insights.command_centre(None, None, 30)),
                "metrics": run(insights.metrics(None, None)), "brain": run(insights.brain(None, None)),
                **{tab: run(insights.studio(None, tab, None)) for tab in ("overview", "visitors", "channels", "tracker", "champions", "lab")},
            }
    finally:
        for item in patches:
            item.stop()


def test_pages_without_ga4_match_the_reference_and_keep_samples_labelled():
    data = pages(None)
    tiles = {tile["label"]: tile for tile in data["metrics"]["tiles"]}
    assert len(tiles) == 16
    assert tiles["Visitor → lead"]["source"] == "Demo"
    assert tiles["WhatsApp median reply"]["source"] == "CMS" and tiles["WhatsApp median reply"]["value"] == "10 min"
    assert data["visitors"]["live"] is False and data["overview"]["pages_live"] is False
    assert data["visitors"]["visitors"]["cities"] and len(data["visitors"]["visitors"]["hours"]) == 24
    assert len(data["dashboard"]["pending"]) == 4 and len(data["dashboard"]["secondary"]) == 3
    assert any("no CRM counsellor" in signal["title"] for signal in data["command"]["signals"])
    assert len(data["channels"]["channels"]) == 10 and data["channels"]["whatsapp_lines"][0]["number"] == "+94 77 *** 4567"
    assert [item["key"] for item in data["brain"]["datasets"]] == ["meta", "channels", "visitors", "pipeline", "team", "partners", "finance", "content"]
    assert next(item for item in data["brain"]["datasets"] if item["key"] == "visitors")["source"] == "Demo"
    assert data["lab"]["recommendations"] == sorted(data["lab"]["recommendations"], key=lambda item: -item["score"])


def test_pages_with_ga4_replace_visitor_samples():
    site = visitors_view(ga4_account(breakdowns=BREAKDOWNS))
    data = pages(site)
    tiles = {tile["label"]: tile for tile in data["metrics"]["tiles"]}
    assert len(tiles) == 16 and tiles["Overseas visitors"]["value"] == "25.0%" and tiles["Overseas visitors"]["source"] == "GA4"
    assert data["visitors"]["live"] is True and data["visitors"]["visitors"]["pages"] == site["pages"]
    assert data["overview"]["pages_live"] is True
    assert next(item for item in data["brain"]["datasets"] if item["key"] == "visitors")["source"] == "GA4"
    assert not any("(demo)" in signal["detail"] and signal["key"] == "visitors" for signal in data["brain"]["signals"])


def test_city_persona_mix_comes_from_pages_read_in_that_city():
    parts = {**BREAKDOWNS, "cities": [{"city": "Kandy", "country": "Sri Lanka", "users": 100}, {"city": "Doha", "country": "Qatar", "users": 50}, {"city": "(not set)", "country": "", "users": 9}],
             "city_pages": [{"city": "Kandy", "path": "/programs/hnd", "views": 10}, {"city": "Doha", "path": "/fees", "views": 10}]}
    cities = visitors_view(ga4_account(breakdowns=parts))["cities"]
    assert [row["city"] for row in cities] == ["Kandy", "Doha"]
    assert cities[0]["lat"] and cities[1]["lat"] is None
    assert cities[0]["s"] == 60 and cities[1]["p"] == 31
