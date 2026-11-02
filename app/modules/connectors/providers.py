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
    if platform not in {"ga4", "gsc", "yt"}:
        raise ValidationError("Reporting integration is not implemented for this platform yet")
    account_id = (account_id or "").strip()
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
        auth = await request_json(client, "POST", "https://oauth2.googleapis.com/token", data={
            "grant_type": "refresh_token", "refresh_token": credentials["refresh_token"],
            "client_id": client_id, "client_secret": client_secret,
        })
        token = auth.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderFailure("Google did not issue an access token. Reauthorize this account.")
        headers = {"Authorization": f"Bearer {token}"}
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
    return {"platform": platform, "account_id": account_id, "start": str(start), "end": str(end),
            "metrics": metrics, "source": "provider", "reporting_note": "Provider-defined reporting dates; recent days may be delayed. No cross-account deduplication."}
