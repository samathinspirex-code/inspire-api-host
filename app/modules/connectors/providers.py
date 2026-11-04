"""Read-only aggregate reports. All URLs are fixed; account IDs are path-escaped."""
import json
import math
import re
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from app.core.errors import ValidationError


class ProviderFailure(Exception):
    """Only fixed, safe messages may cross into a CMS response or persisted error."""


SITE_URL = "https://inspirecollege.lk/"
# pagePath excludes query strings, so form values and click IDs never reach reports.
# name: (dimensions, metrics, row limit, dimension keys, metric keys)
GA4_BREAKDOWNS = {
    "pages": (["pagePath"], ["screenPageViews", "activeUsers", "userEngagementDuration"], 20, ["path"], ["views", "users", "engagement_seconds"]),
    "countries": (["country"], ["sessions"], 50, ["name"], ["sessions"]),
    "devices": (["deviceCategory"], ["sessions", "userEngagementDuration"], 5, ["name"], ["sessions", "engagement_seconds"]),
    "channels": (["sessionDefaultChannelGroup"], ["sessions"], 12, ["name"], ["sessions"]),
    "events": (["eventName"], ["eventCount"], 100, ["name"], ["count"]),
    "visitor_types": (["newVsReturning"], ["activeUsers"], 5, ["name"], ["users"]),
    "hours": (["hour"], ["sessions"], 24, ["hour"], ["sessions"]),
    "cities": (["city", "country"], ["activeUsers"], 40, ["city", "country"], ["users"]),
    "city_pages": (["city", "pagePath"], ["screenPageViews"], 500, ["city", "path"], ["views"]),
}
GA4_BATCH_SIZE = 5  # The Data API's limit per batchRunReports call.


async def ga4_breakdowns(client, account_id: str, headers: dict, start, end) -> dict:
    names = list(GA4_BREAKDOWNS)
    result = {}
    for offset in range(0, len(names), GA4_BATCH_SIZE):
        chunk = names[offset:offset + GA4_BATCH_SIZE]
        requests = [{"dateRanges": [{"startDate": str(start), "endDate": str(end)}],
                     "dimensions": [{"name": dimension} for dimension in GA4_BREAKDOWNS[key][0]],
                     "metrics": [{"name": name} for name in GA4_BREAKDOWNS[key][1]],
                     "orderBys": [{"metric": {"metricName": GA4_BREAKDOWNS[key][1][0]}, "desc": True}], "limit": GA4_BREAKDOWNS[key][2]}
                    for key in chunk]
        data = await request_json(client, "POST", f"https://analyticsdata.googleapis.com/v1beta/properties/{account_id}:batchRunReports",
                                  headers=headers, json={"requests": requests})
        reports = data["reports"]
        if len(reports) != len(requests):
            raise ValueError("Unexpected report count")
        for key, report in zip(chunk, reports):
            _, metrics, _, labels, values = GA4_BREAKDOWNS[key]
            if [h["name"] for h in report["metricHeaders"]] != metrics:
                raise ValueError("Unexpected metrics")
            result[key] = [{**dict(zip(labels, [v["value"] for v in row["dimensionValues"]])),
                            **dict(zip(values, [number(v["value"]) for v in row["metricValues"]]))}
                           for row in report.get("rows", [])]
    return result


async def _google_token(client, credentials: dict, client_id: str, client_secret: str) -> str:
    auth = await request_json(client, "POST", "https://oauth2.googleapis.com/token", data={
        "grant_type": "refresh_token", "refresh_token": credentials["refresh_token"],
        "client_id": client_id, "client_secret": client_secret,
    })
    token = auth.get("access_token")
    if not isinstance(token, str) or not token:
        raise ProviderFailure("Google did not issue an access token. Reauthorize this account.")
    return token


async def ga4_realtime(account_id: str, credentials: dict, client_id: str, client_secret: str, *, transport=None) -> dict:
    """People on the site in the last 30 minutes. GA4 reports these as totals, never individual visitors."""
    if not re.fullmatch(r"[0-9]+", account_id or ""):
        raise ValidationError("Use the numeric GA4 property ID")
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, transport=transport) as client:
        headers = {"Authorization": f"Bearer {await _google_token(client, credentials, client_id, client_secret)}"}
        result = {}
        for key, dimension in (("countries", "country"), ("pages", "unifiedScreenName"), ("devices", "deviceCategory")):
            data = await request_json(client, "POST", f"https://analyticsdata.googleapis.com/v1beta/properties/{account_id}:runRealtimeReport",
                                      headers=headers, json={"dimensions": [{"name": dimension}], "metrics": [{"name": "activeUsers"}], "limit": 8})
            result[key] = [{"name": row["dimensionValues"][0]["value"], "value": number(row["metricValues"][0]["value"])} for row in data.get("rows", [])]
    return {"available": True, "active": sum(row["value"] for row in result["devices"]), **result}


async def gtm_report(container_id: str, *, transport=None) -> dict:
    """Reads the public published container and the public home page; no Google login needed."""
    if not re.fullmatch(r"GTM-[A-Z0-9]{4,12}", container_id):
        raise ValidationError("Use the container ID beginning with GTM-")
    async with httpx.AsyncClient(timeout=25, follow_redirects=False, transport=transport) as client:
        container = await client.get(f"https://www.googletagmanager.com/gtm.js?id={container_id}")
        if not container.is_success:
            raise ProviderFailure("This container is not published. Publish a version in Tag Manager, then sync again.")
        site = await client.get(SITE_URL)
    source = container.text
    ga4_ids = sorted(set(re.findall(r'"vtp_tagId":"(G-[A-Z0-9]+)"', source)))
    ga4_tags = len(re.findall(r'"function":"__googtag"[^{}]*?"vtp_tagId":"G-[A-Z0-9]+"', source))
    html = site.text if site.is_success else ""
    installed = container_id in html
    hard_coded = sorted(set(re.findall(r"gtag/js\?id=(G-[A-Z0-9]+)", html)) & set(ga4_ids))
    warnings = []
    if not installed:
        warnings.append("The container was not found on the website home page.")
    if hard_coded:
        warnings.append(f"{', '.join(hard_coded)} is hard-coded on the website and also in this container, so GA4 can count twice.")
    if ga4_tags > 1:
        warnings.append(f"This container has {ga4_tags} Google tags for GA4. Keep one to avoid double counting.")
    return {"platform": "gtm", "account_id": container_id, "start": None, "end": None,
            "metrics": {"ga4_tags": ga4_tags},
            "checks": {"published": True, "installed_on_site": installed, "ga4_ids": ga4_ids, "warnings": warnings},
            "source": "provider",
            "reporting_note": " ".join(warnings) or "Container is published and on the home page. This confirms installation, not that each event is delivered."}


META_API = "https://graph.facebook.com/v25.0"
# Meta's own lifetime account states. Anything not listed here is reported as active-or-unknown, without a warning.
META_ACCOUNT_WARNINGS = {
    2: "Meta has disabled this ad account.",
    3: "Meta marks this ad account as unsettled: a payment is outstanding, so ads may stop running.",
    7: "This ad account is pending a risk review at Meta.",
    8: "This ad account is pending settlement at Meta.",
    9: "This ad account is in a payment grace period at Meta.",
    100: "This ad account is pending closure at Meta.",
    101: "This ad account is closed at Meta.",
}


async def _meta_get(client, path: str, token: str, params: dict) -> dict:
    # The token travels only in this header, and error bodies never leave this function.
    response = await client.get(f"{META_API}/{path}", headers={"Authorization": f"Bearer {token}"}, params=params)
    if response.is_success:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected provider response shape")
        return data
    try:
        code = response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        code = None
    if code in (4, 17, 32, 613) or response.status_code == 429:
        raise ProviderFailure("Meta rate limit reached. Try again later.")
    if code in (10, 190, 200) or response.status_code in (401, 403):
        raise ProviderFailure("Meta rejected the token. Check the System User token, its ads_read permission and this ad account's access.")
    raise ProviderFailure("Meta could not supply the report. Check the ad account ID and access.")


async def _meta_rows(client, account_id: str, token: str, params: dict) -> list[dict]:
    rows, after = [], None
    while True:
        data = await _meta_get(client, f"act_{account_id}/insights", token, {**params, "limit": 500, **({"after": after} if after else {})})
        rows += data["data"]
        paging = data.get("paging") or {}
        after = (paging.get("cursors") or {}).get("after") if paging.get("next") else None
        if not after:
            return rows


def _meta_action(row: dict, action_type: str) -> float:
    return sum(number(item["value"]) for item in row.get("actions") or [] if item["action_type"] == action_type)


def _meta_leads(row: dict) -> float:
    # Region rows carry only the grouped count; for this account it equals `lead` on every other view.
    return _meta_action(row, "lead") or _meta_action(row, "onsite_conversion.lead_grouped")


async def meta_report(account_id: str, token: str, *, transport=None) -> dict:
    """Read-only spend and results. `lead` is Meta's own lead count; chats are kept apart and never counted as leads."""
    end = datetime.now(ZoneInfo("Asia/Colombo")).date() - timedelta(days=1)
    start = end - timedelta(days=29)
    # The unified setting makes the counts follow each campaign's attribution, as Ads Manager does.
    common = {"time_range": json.dumps({"since": str(start), "until": str(end)}), "use_unified_attribution_setting": "true"}
    async with httpx.AsyncClient(timeout=60, follow_redirects=False, transport=transport) as client:
        info = await _meta_get(client, f"act_{account_id}", token, {"fields": "name,account_status,currency,timezone_name"})
        totals = await _meta_rows(client, account_id, token, {**common, "fields": "spend,reach,impressions,inline_link_clicks,actions"})
        campaigns = await _meta_rows(client, account_id, token, {**common, "level": "campaign", "fields": "campaign_name,objective,spend,reach,impressions,inline_link_clicks,actions"})
        days = await _meta_rows(client, account_id, token, {**common, "time_increment": 1, "fields": "spend,inline_link_clicks,actions"})
        people = await _meta_rows(client, account_id, token, {**common, "breakdowns": "age,gender", "fields": "spend,actions"})
        regions = await _meta_rows(client, account_id, token, {**common, "breakdowns": "region", "fields": "spend,inline_link_clicks,actions"})
    if len(totals) > 1:
        raise ValueError("Unexpected aggregate rows")
    total = totals[0] if totals else {}
    chats = "onsite_conversion.messaging_conversation_started_7d"
    warning = META_ACCOUNT_WARNINGS.get(info.get("account_status"))
    return {
        "platform": "meta", "account_id": account_id, "start": str(start), "end": str(end),
        "account_name": str(info.get("name") or ""), "currency": str(info["currency"]), "account_status": info.get("account_status"),
        "warnings": [warning] if warning else [],
        "metrics": {"spend": number(total.get("spend", 0)), "reach": number(total.get("reach", 0)), "impressions": number(total.get("impressions", 0)),
                    "link_clicks": number(total.get("inline_link_clicks", 0)), "leads": _meta_leads(total), "conversations": _meta_action(total, chats)},
        "breakdowns": {
            "campaigns": sorted(({"name": str(row["campaign_name"]), "objective": str(row.get("objective") or ""), "spend": number(row.get("spend", 0)),
                                  "impressions": number(row.get("impressions", 0)), "reach": number(row.get("reach", 0)), "clicks": number(row.get("inline_link_clicks", 0)),
                                  "landing_views": _meta_action(row, "landing_page_view"), "leads": _meta_leads(row), "conversations": _meta_action(row, chats),
                                  "website_leads": _meta_action(row, "offsite_conversion.fb_pixel_lead")} for row in campaigns), key=lambda item: -item["spend"]),
            "days": sorted(({"date": row["date_start"], "spend": number(row.get("spend", 0)), "clicks": number(row.get("inline_link_clicks", 0)), "leads": _meta_leads(row)} for row in days), key=lambda item: item["date"]),
            "people": [{"age": row["age"], "gender": row["gender"], "spend": number(row.get("spend", 0)), "leads": _meta_leads(row)} for row in people],
            "regions": [{"name": row["region"], "spend": number(row.get("spend", 0)), "clicks": number(row.get("inline_link_clicks", 0)), "leads": _meta_leads(row)} for row in regions],
        },
        "source": "provider",
        "reporting_note": "Meta's own counts, using each campaign's attribution setting. Recent days can still change. Chats are not counted as leads.",
    }


MAILJET_FIELDS = {"sent": "MessageSentCount", "blocked": "MessageBlockedCount", "hard_bounced": "MessageHardBouncedCount", "soft_bounced": "MessageSoftBouncedCount",
                  "queued": "MessageQueuedCount", "deferred": "MessageDeferredCount", "opened": "MessageOpenedCount", "clicked": "MessageClickedCount",
                  "spam": "MessageSpamCount", "unsubscribed": "MessageUnsubscribedCount"}
EMAIL_ALERT_RATE = 5.0  # Percent of processed emails, above which a problem is worth raising.


def _email_warnings(metrics: dict, processed: float) -> list[str]:
    warnings = []
    if not processed:
        return warnings
    blocked = metrics["blocked"] / processed * 100
    bounced = (metrics["hard_bounced"] + metrics["soft_bounced"]) / processed * 100
    if blocked >= EMAIL_ALERT_RATE:
        warnings.append(f"{blocked:.1f}% of emails were blocked by Mailjet, so those people never received them.")
    if bounced >= EMAIL_ALERT_RATE:
        warnings.append(f"{bounced:.1f}% of emails bounced. Check for wrong addresses.")
    if metrics["spam"]:
        warnings.append(f"{metrics['spam']:.0f} emails were reported as spam.")
    if metrics["sent"] and not metrics["opened"] and not metrics["clicked"]:
        warnings.append("Mailjet recorded no opens or clicks. Tracking may be off, so this does not show that nobody read them.")
    return warnings


async def mailjet_report(key: str, secret: str, *, transport=None) -> dict:
    """Totals only. No recipient address or subject is requested or stored."""
    end = datetime.now(ZoneInfo("Asia/Colombo")).date() - timedelta(days=1)
    start = end - timedelta(days=29)
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport, auth=(key, secret)) as client:
        data = await request_json(client, "GET", "https://api.mailjet.com/v3/REST/statcounters", params={
            "CounterSource": "APIKey", "CounterTiming": "Message", "CounterResolution": "Day", "Limit": 40,
            "FromTS": f"{start}T00:00:00", "ToTS": f"{end}T23:59:59"})
        campaigns = await request_json(client, "GET", "https://api.mailjet.com/v3/REST/campaign", params={"FromTS": f"{start}T00:00:00", "Limit": 1})
    days = sorted(({"date": row["Timeslice"][:10], "processed": number(row["Total"]), **{name: number(row[field]) for name, field in MAILJET_FIELDS.items()}}
                   for row in data["Data"] if str(start) <= row["Timeslice"][:10] <= str(end)), key=lambda item: item["date"])
    metrics = {name: sum(day[name] for day in days) for name in MAILJET_FIELDS}
    processed = sum(day["processed"] for day in days)
    return {"platform": "email", "account_id": "mailjet", "start": str(start), "end": str(end), "provider": "Mailjet",
            "metrics": {"processed": processed, **metrics}, "campaigns": number(campaigns.get("Total", 0)),
            "warnings": _email_warnings(metrics, processed), "breakdowns": {"days": days}, "source": "provider",
            "reporting_note": "Mailjet's own totals for system emails. Opens and clicks are only counted when tracking is on. Email is not linked to leads or enrolments."}


def number(value) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite provider metric")
    return result


async def request_json(client, method, url, **kwargs):
    response = await client.request(method, url, **kwargs)
    if response.status_code in (401, 403):
        raise ProviderFailure("Access denied. Reauthorize this account and check its reporting permissions.")
    if response.status_code == 429:
        raise ProviderFailure("Provider rate limit reached. Try again later.")
    if not response.is_success:
        raise ProviderFailure("Provider could not supply the report. Check account access and API configuration.")
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected provider response shape")
    return data


async def fetch_report(platform: str, account_id: str, credentials: dict, client_id: str, client_secret: str,
                       *, transport=None) -> dict:
    if platform not in {"ga4", "gsc", "yt", "gtm", "meta", "email"}:
        raise ValidationError("Reporting integration is not implemented for this platform yet")
    account_id = (account_id or "").strip()
    if platform == "gtm":
        return await gtm_report(account_id, transport=transport)
    if platform == "email":
        if not (credentials.get("key") and credentials.get("secret")):
            raise ValidationError("Configure the Mailjet API keys on the server first")
        return await mailjet_report(credentials["key"], credentials["secret"], transport=transport)
    if platform == "meta":
        if not re.fullmatch(r"[0-9]+", account_id):
            raise ValidationError("Use the numeric Meta ad account ID, without act_")
        if not credentials.get("token"):
            raise ValidationError("Configure the Meta System User token on the server first")
        return await meta_report(account_id, credentials["token"], transport=transport)
    if platform == "ga4" and not re.fullmatch(r"[0-9]+", account_id):
        raise ValidationError("Use the numeric GA4 property ID, not the G- measurement ID")
    if platform == "gsc" and not account_id.startswith(("https://", "http://", "sc-domain:")):
        raise ValidationError("Use the exact verified Search Console property URL or sc-domain property")
    if platform == "yt" and not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", account_id):
        raise ValidationError("Use the YouTube channel ID beginning with UC")
    if not (credentials.get("refresh_token") and client_id and client_secret):
        raise ValidationError("Configure this connector's Google refresh token and the server OAuth client first")
    end = datetime.now(ZoneInfo("Asia/Colombo")).date() - timedelta(days=1)
    start = end - timedelta(days=29)
    async with httpx.AsyncClient(timeout=25, follow_redirects=False, transport=transport) as client:
        headers = {"Authorization": f"Bearer {await _google_token(client, credentials, client_id, client_secret)}"}
        if platform == "ga4":
            names = ["activeUsers", "sessions", "screenPageViews", "engagedSessions"]
            data = await request_json(client, "POST", f"https://analyticsdata.googleapis.com/v1beta/properties/{account_id}:runReport",
                                      headers=headers, json={"dateRanges": [{"startDate": str(start), "endDate": str(end)}],
                                      "metrics": [{"name": name} for name in names]})
            if [h["name"] for h in data["metricHeaders"]] != names:
                raise ValueError("Unexpected metrics")
            rows = data.get("rows", [])
            if len(rows) > 1:
                raise ValueError("Unexpected aggregate rows")
            metrics = dict(zip(names, [number(v["value"]) for v in rows[0]["metricValues"]])) if rows else dict.fromkeys(names, 0)
            if len(metrics) != len(names):
                raise ValueError("Incomplete metrics")
            breakdowns = await ga4_breakdowns(client, account_id, headers, start, end)
        elif platform == "gsc":
            data = await request_json(client, "POST", f"https://www.googleapis.com/webmasters/v3/sites/{quote(account_id, safe='')}/searchAnalytics/query",
                                      headers=headers, json={"startDate": str(start), "endDate": str(end), "dataState": "final"})
            rows = data.get("rows", [])
            if len(rows) > 1:
                raise ValueError("Unexpected aggregate rows")
            metrics = {name: number(rows[0][name]) for name in ("clicks", "impressions", "ctr", "position")} if rows else {"clicks": 0, "impressions": 0}
            breakdowns = {}
            for key, dimension in (("queries", "query"), ("pages", "page")):
                detail = await request_json(client, "POST", f"https://www.googleapis.com/webmasters/v3/sites/{quote(account_id, safe='')}/searchAnalytics/query",
                                            headers=headers, json={"startDate": str(start), "endDate": str(end), "dataState": "final", "dimensions": [dimension], "rowLimit": 25})
                breakdowns[key] = [{"name": row["keys"][0], **{name: number(row[name]) for name in ("clicks", "impressions", "ctr", "position")}}
                                   for row in detail.get("rows", [])]
        else:
            names = ["views", "estimatedMinutesWatched", "subscribersGained", "subscribersLost"]
            data = await request_json(client, "GET", "https://youtubeanalytics.googleapis.com/v2/reports", headers=headers,
                                      params={"ids": f"channel=={account_id}", "startDate": str(start), "endDate": str(end), "metrics": ",".join(names)})
            if [h["name"] for h in data["columnHeaders"]] != names:
                raise ValueError("Unexpected metrics")
            rows = data.get("rows", [])
            if len(rows) > 1:
                raise ValueError("Unexpected aggregate rows")
            metrics = dict(zip(names, [number(v) for v in rows[0]])) if rows else dict.fromkeys(names, 0)
            if len(metrics) != len(names):
                raise ValueError("Incomplete metrics")
    report = {"platform": platform, "account_id": account_id, "start": str(start), "end": str(end),
              "metrics": metrics, "source": "provider", "reporting_note": "Provider-defined reporting dates; recent days may be delayed. No cross-account deduplication."}
    if platform in {"ga4", "gsc"}:
        report["breakdowns"] = breakdowns
    return report
