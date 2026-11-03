"""Read-only aggregate reports. All URLs are fixed; account IDs are path-escaped."""
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
    if platform not in {"ga4", "gsc", "yt", "gtm"}:
        raise ValidationError("Reporting integration is not implemented for this platform yet")
    account_id = (account_id or "").strip()
    if platform == "gtm":
        return await gtm_report(account_id, transport=transport)
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
    if platform == "ga4":
        report["breakdowns"] = breakdowns
    return report
