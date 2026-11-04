import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.insights import meta as meta_module

import httpx
import pytest

from app.core.errors import ValidationError
from app.modules.connectors import providers, registry
from app.modules.insights import finance as finance_module, lms as lms_module, service as insights, studio_data
from app.modules.insights.website import ga4_events, latest_report, search_view, visitors_view
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


def test_crm_backed_channel_rows_are_live_and_other_rows_stay_demo():
    live = {"WhatsApp Business": {"spend": 0, "leads": 319, "enrolled": 4, "source": "cms", "live": True, "trend": [1] * 8},
            "Website & SEO": {"spend": 0, "leads": 97, "enrolled": 2, "source": "cms", "live": True, "trend": [1] * 8}}
    rows = {item["name"]: item for item in insights._channels(live)}
    assert rows["WhatsApp Business"]["leads"] == 319 and rows["WhatsApp Business"]["spend"] == 0
    assert rows["Website & SEO"]["live"] and rows["Website & SEO"]["spend"] == 0
    assert rows["Meta Ads"]["source"] == "demo" and len(rows) == 10


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


FINANCE_ROW = {"demo_data": False, "invoices": 10, "payments": 6, "open_invoices": 4, "outstanding": 1500000, "overdue_7d": 2, "overdue_amount": 400000, "due_invoices": 8, "paid_on_time": 6, "collected_30d": 90000}
FINANCE_EMPTY = finance_module.summarize({**dict.fromkeys(FINANCE_ROW, 0), "demo_data": False}, "sandbox")
LMS_DATA = lms_module.summarize({"records": 100, "present": 70, "records_30d": 40, "present_30d": 30, "sessions_30d": 4, "enrolments": 50, "withdrawn": 5, "enrolled_students": 40, "active_30d": 30},
                                [{"intake": "Oct 2026", "students": 2, "cells": [100, 50]}])


def pages(site, finance=None, meta=None, email=False):
    bot = summarize([lead(1, reply_minutes=10), lead(2, counsellor=None)], {"student": 1, "counsellor": 1}, datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3))
    crm = {"source_flow": {"total": 0, "outcomes": [], "sources": []}, "source_programme": {"programmes": [], "rows": []},
           "counsellor_source": {"sources": [], "rows": []}, "channel_counts": {}, "whatsapp_numbers": {7: "+94 77 123 4567"}, "tracked_people": [],
           "referral_summary": {"funnel": {"leads": 0, "contacted": 0, "counselled": 0, "enrolled": 0}, "codes": [], "invites": []}}
    patches = [patch.object(studio_data, name, AsyncMock(return_value=value)) for name, value in crm.items()]
    for item in patches:
        item.start()
    try:
        with patch.object(insights, "load_snapshot", AsyncMock(return_value=SNAP)), patch.object(insights, "get_mission", AsyncMock(return_value=GOAL)), \
                patch.object(insights, "load_meta", AsyncMock(return_value=meta)), patch.object(insights, "_email_connected", AsyncMock(return_value=email)), patch.object(insights, "load_finance", AsyncMock(return_value=finance or FINANCE_EMPTY)), patch.object(insights, "load_lms", AsyncMock(return_value=LMS_DATA)), patch.object(insights, "load_whatsapp", AsyncMock(return_value=bot)), patch.object(insights, "_website", AsyncMock(return_value=site)), patch.object(insights, "_search", AsyncMock(return_value=None)):
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


def test_lms_figures_replace_demo_retention_and_attendance():
    data = pages(None)["metrics"]
    nodes = {node["id"]: node for node in data["tree"]}
    assert nodes["att"]["value"] == "75.0%" and nodes["att"]["source"] == "LMS"
    assert nodes["ret"]["value"] == "90.0% (dropout 10.0%)"
    tile = next(item for item in data["tiles"] if item["label"] == "Dropout ratio")
    assert tile["value"] == "10.0%" and tile["source"] == "LMS"
    assert data["cohorts"] == LMS_DATA["cohorts"] and data["cohorts_source"] == "cms"
    brain = pages(None)["brain"]
    assert not any("stated" in item["note"] for item in brain["predictions"])


def test_lms_summary_invents_nothing_when_empty():
    empty = lms_module.summarize(dict.fromkeys(("records", "present", "records_30d", "present_30d", "sessions_30d", "enrolments", "withdrawn", "enrolled_students", "active_30d"), 0), [])
    assert empty["attendance"] is None and empty["dropout"] is None and empty["inactive_share"] is None and not empty["connected"]
    assert "no attendance records" in lms_module.context_line(empty)


def test_cohort_cells_are_measured_per_intake():
    from datetime import date
    rows = lms_module.build_cohorts([(1, date(2026, 9, 1)), (2, date(2026, 9, 1)), (3, date(2026, 11, 1))],
                                    [(1, date(2026, 9, 1)), (2, date(2026, 9, 1)), (1, date(2026, 10, 1))], date(2026, 11, 3))
    assert rows[0] == {"intake": "Sep 2026", "students": 2, "cells": [100, 50, 0]}
    assert rows[1] == {"intake": "Nov 2026", "students": 1, "cells": [0]}


def test_brain_platform_status_comes_from_lms_records():
    def status(**counts):
        base = dict.fromkeys(("records", "present", "records_30d", "present_30d", "sessions_30d", "enrolments", "withdrawn", "enrolled_students", "active_30d"), 0)
        return next(p for p in registry.platforms_for([], None, lms_module.summarize({**base, **counts}, [])) if p["id"] == "brain")["status"]
    assert status(records=5, records_30d=5) == "Receiving data"
    assert status(records=5) == "No recent activity"
    assert status() == "Not connected"


def test_search_view_shows_real_figures_and_short_page_paths():
    row = {"clicks": 3, "impressions": 90, "ctr": 0.033, "position": 7.2}
    account = {"platform": "gsc", "account_id": "https://inspirecollege.lk/", "last_synced_at": datetime.now(timezone.utc).isoformat(), "report": {
        "start": "2026-10-05", "end": "2026-11-03", "metrics": {"clicks": 10, "impressions": 500, "ctr": 0.02, "position": 11.26},
        "breakdowns": {"queries": [{"name": "inspire college", **row}], "pages": [{"name": "https://inspirecollege.lk/programs/hnd", **row}, {"name": "https://inspirecollege.lk/", **row}]}}}
    view = search_view(account)
    assert (view["clicks"], view["ctr"], view["position"]) == (10, 2.0, 11.3)
    assert [page["name"] for page in view["pages"]] == ["/programs/hnd", "/"]
    assert search_view(None) is None


def test_payments_ledger_replaces_demo_fee_figures():
    data = pages(None, finance_module.summarize(FINANCE_ROW, "sandbox"))
    outstanding = next(item for item in data["dashboard"]["kpis"] if item["label"] == "Outstanding")
    assert outstanding["value"] == "LKR 1.5M" and outstanding["source"] == "cms"
    overdue = next(item for item in data["dashboard"]["pending"] if "overdue" in item["label"])
    assert overdue["count"] == 2 and overdue["source"] == "cms"
    tiles = {tile["label"]: tile for tile in data["metrics"]["tiles"]}
    assert tiles["Outstanding fees"]["source"] == "Payments" and tiles["Payments on time"]["value"] == "75.0%"
    assert next(node for node in data["metrics"]["tree"] if node["id"] == "pay")["value"] == "75.0%"
    assert next(item for item in data["brain"]["datasets"] if item["key"] == "finance")["source"] == "Payments"
    assert not any("(demo)" in signal["detail"] for signal in data["brain"]["signals"] if signal["key"] == "finance")


def test_empty_ledger_shows_real_zero_not_demo():
    data = pages(None)
    assert next(item for item in data["dashboard"]["kpis"] if item["label"] == "Outstanding")["value"] == "LKR 0"
    assert next(item for item in data["dashboard"]["pending"] if "overdue" in item["label"])["count"] == 0
    assert next(tile for tile in data["metrics"]["tiles"] if tile["label"] == "Payments on time")["value"] == "—"
    assert "not that fees are fully collected" in finance_module.context_line(FINANCE_EMPTY)


def test_demo_ledger_or_missing_schema_keeps_figures_labelled_demo():
    assert finance_module.summarize({**FINANCE_ROW, "demo_data": True}, "sandbox")["live"] is False
    assert finance_module.summarize(None, "sandbox")["live"] is False
    data = pages(None, {"live": False, "mode": "sandbox"})
    outstanding = next(item for item in data["dashboard"]["kpis"] if item["label"] == "Outstanding")
    assert outstanding["value"] == "LKR 4.2M" and outstanding["source"] == "demo"


def test_payhere_status_never_claims_live_in_sandbox():
    def status(**extra):
        finance = {**finance_module.summarize(FINANCE_ROW, "sandbox"), **extra}
        return next(p for p in registry.platforms_for([], None, None, finance) if p["id"] == "payhere")["status"]
    assert status() == "Ledger linked · sandbox"
    assert status(mode="live") == "Receiving data"
    assert status(mode="live", payments=0) == "Connected · live"


def meta_account_row(spend=100.0, currency="LKR", synced=None):
    days = [{"date": f"2026-10-{day:02d}", "spend": 10.0, "clicks": 1.0, "leads": 2.0 if day <= 7 else 1.0} for day in range(1, 31)]
    return {"platform": "meta", "name": "Inspire", "last_synced_at": (synced or datetime.now(timezone.utc)).isoformat(), "report": {
        "account_id": "123", "start": "2026-10-01", "end": "2026-10-30", "currency": currency, "warnings": ["Unsettled"],
        "metrics": {"spend": spend, "reach": 80.0, "impressions": 90.0, "link_clicks": 30.0, "leads": 37.0, "conversations": 50.0},
        "breakdowns": {"days": days, "campaigns": [{"name": "A", "spend": spend, "impressions": 90.0, "reach": 80.0, "clicks": 30.0, "landing_views": 1.0, "leads": 37.0, "conversations": 50.0, "website_leads": 0.0}],
                       "people": [{"age": "18-24", "gender": "male", "spend": 60.0, "leads": 20.0}, {"age": "18-24", "gender": "female", "spend": 30.0, "leads": 15.0}, {"age": "Unknown", "gender": "unknown", "spend": 10.0, "leads": 2.0}],
                       "regions": [{"name": "Western Province", "spend": 90.0, "clicks": 20.0, "leads": 30.0}, {"name": "Unknown", "spend": 10.0, "clicks": 1.0, "leads": 0.0}]}}}


def test_meta_view_matches_the_studio_shape_and_never_mixes_currencies():
    usd = meta_account_row(spend=9999.0, currency="USD")
    lkr = meta_account_row()
    assert meta_module.meta_account([usd]) is None
    assert meta_module.meta_account([usd, lkr]) is lkr
    view = meta_module.meta_view(lkr)
    assert view["live"] is True and view["stale"] is False and view["leads"] == 37
    assert view["men_leads"][0] == 20 and view["women_spend"][0] == 30 and view["unknown_leads"] == 2
    assert view["regions"] == {"Western": {"spend": 90.0, "clicks": 20.0, "leads": 30.0}}
    assert view["days"][0] == "10-01" and sum(view["cost"]) == 300
    assert meta_module.weekly_leads(view) == [12, 7, 7, 7]  # full weeks only, counted back from the latest day
    assert meta_module.cpl_weeks(view) == (5, 10)


def test_old_meta_report_is_marked_stale():
    view = meta_module.meta_view(meta_account_row(synced=datetime.now(timezone.utc) - timedelta(days=9)))
    assert view["stale"] is True


def test_live_meta_replaces_every_meta_sample():
    view = {**meta_module.meta_view(meta_account_row()), "crm": {"leads": 30, "contacted": 20, "enrolled": 4}}
    data = pages(None, meta=view)
    tiles = {tile["label"]: tile for tile in data["metrics"]["tiles"]}
    assert tiles["Meta spend · 30d"]["source"] == "Meta" and tiles["Meta cost per lead"]["source"] == "Meta"
    assert tiles["Meta cost per lead"]["delta"] == "last 30 days"
    assert next(node for node in data["metrics"]["tree"] if node["id"] == "paid")["source"] == "Meta"
    assert data["overview"]["meta"]["live"] is True and data["overview"]["meta_info"]["account"] == "Inspire"
    row = next(item for item in data["channels"]["channels"] if item["name"] == "Meta Ads")
    assert row["source"] == "connector" and row["live"] is True and row["enrolled"] == 4 and row["spend"] == 100
    assert data["lab"]["meta_campaigns"][0]["name"] == "A"
    assert next(item for item in data["brain"]["datasets"] if item["key"] == "meta")["source"] == "Meta"
    signal = next(item for item in data["brain"]["signals"] if item["key"] == "meta")
    assert "demo" not in signal["detail"]
    assert not any("demo" in " ".join(item["sources"]).lower() for item in data["lab"]["recommendations"])
    assert "Unsettled" in meta_module.summary(view) and "not counted as leads" in meta_module.summary(view)


def test_without_meta_the_samples_stay_labelled_demo():
    data = pages(None)
    assert next(tile for tile in data["metrics"]["tiles"] if tile["label"] == "Meta spend · 30d")["source"] == "Demo"
    assert data["overview"]["meta_info"]["live"] is False
    assert next(item for item in data["channels"]["channels"] if item["name"] == "Meta Ads")["source"] == "demo"


def test_meta_crm_query_binds_real_dates():
    # asyncpg rejects text for a date parameter, which took every dashboard down once Meta had synced.
    result = MagicMock()
    result.mappings.return_value.first.return_value = {"leads": 3, "contacted": 2, "enrolled": 1}
    db = MagicMock(execute=AsyncMock(return_value=result))
    assert run(meta_module.crm_results(db, {"start": "2026-10-05", "end": "2026-11-03"})) == {"leads": 3, "contacted": 2, "enrolled": 1}
    params = db.execute.await_args.args[1]
    assert params["start"] == date(2026, 10, 5) and params["end"] == date(2026, 11, 3)


def test_connected_mailjet_removes_the_email_samples():
    assert next(item for item in pages(None)["channels"]["channels"] if item["name"] == "Email")["source"] == "demo"
    row = next(item for item in pages(None, email=True)["channels"]["channels"] if item["name"] == "Email")
    assert row["source"] == "connector" and row["live"] is True and (row["spend"], row["leads"], row["enrolled"]) == (0, 0, 0)
