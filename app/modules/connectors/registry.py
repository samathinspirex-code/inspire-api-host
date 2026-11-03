"""Shared, honest readiness for the entire Growth & Data source catalogue."""
from datetime import datetime, timedelta, timezone

from app.modules.insights.demo_data import GTM_EVENTS, PLATFORMS

# A saved ID, a configured credential and an ingested report are different things.
REQUIREMENTS = {
    "meta": ("Ad account ID and Meta app approval with ads_read", "Paid campaigns, spend and attribution"),
    "capi": ("Dataset/pixel ID, server token and consent-aware event outbox", "Lead and enrolment feedback"),
    "ga4": ("Numeric GA4 property ID and Google analytics.readonly consent", "Website sessions and engagement"),
    "gtm": ("GTM container ID (GTM-XXXX) published on the website. No Google login is needed for this check", "Installation check and duplicate GA4 tag warnings"),
    "gads": ("Customer ID, developer token and Google Ads consent", "Search campaigns and offline conversions"),
    "gsc": ("Verified site URL or sc-domain property and webmasters.readonly consent", "Organic search clicks and impressions"),
    "tiktok": ("Advertiser ID and approved TikTok Business app", "Paid video campaigns and lead forms"),
    "wa": ("The WhatsApp bot's INSPIRE_CRM_API_URL and INSPIRE_CRM_API_KEY, and counsellor emails matching CRM users", "Bot handovers, chat timelines and logged reply times"),
    "ig": ("Professional Instagram account and approved insights permissions", "Organic content performance"),
    "li": ("Ad account ID and approved LinkedIn Marketing API access", "Professional audience campaigns"),
    "yt": ("Channel ID and Google yt-analytics.readonly consent", "Video views and watch time"),
    "email": ("Choose Email and SMS providers and configure delivery webhooks", "Campaign delivery and engagement"),
    "payhere": ("Existing Payments configuration and verified payment notifications", "Fees and payment reconciliation"),
    "brain": ("Map existing LMS records to attendance and retention definitions", "Student activity and retention"),
    "forms": ("Existing website form submission route", "Enquiries into the CRM"),
    "calls": ("Choose a telephony provider and configure signed call webhooks", "Calls, outcomes and durations"),
}
SYNC_PLATFORMS = {"ga4", "gsc", "yt", "gtm"}


def account_state(account: dict, configured: bool, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    synced = account.get("last_synced_at")
    if isinstance(synced, str):
        synced = datetime.fromisoformat(synced)
    if synced and synced.tzinfo is None:
        synced = synced.replace(tzinfo=timezone.utc)
    stale = bool(synced and now - synced > timedelta(hours=48))
    checks = (account.get("report") or {}).get("checks") if isinstance(account.get("report"), dict) else None
    if account.get("last_error"):
        label, tone = "Sync failed", "bad"
    elif synced and checks is not None and not stale:
        label, tone = ("Check tags", "warn") if checks.get("warnings") else ("Container verified", "good")
    elif synced:
        label, tone = ("Stale", "warn") if stale else ("Receiving data", "good")
    elif configured and account["platform"] in SYNC_PLATFORMS:
        label, tone = "Ready to sync", "warn"
    elif account["platform"] in SYNC_PLATFORMS:
        label, tone = "Credentials needed", "warn"
    else:
        label, tone = "Integration pending", "warn"
    return {"status_label": label, "tone": tone, "stale": stale, "can_sync": configured and account["platform"] in SYNC_PLATFORMS}


def platforms_for(accounts: list[dict], whatsapp: dict | None = None) -> list[dict]:
    result = []
    for definition in PLATFORMS:
        platform = definition["id"]
        matches = [a for a in accounts if a["platform"] == platform]
        healthy = [a for a in matches if a.get("last_synced_at") and a.get("tone") == "good"]
        status, tone = "Not connected", ""
        if matches:
            status, tone = ("Receiving data", "good") if healthy else (matches[0]["status_label"], matches[0]["tone"])
        if platform == "forms":
            status, tone = "CRM route available", "warn"
        source = "connector" if healthy else "unavailable"
        requirements, feeds = REQUIREMENTS[platform]
        if platform == "wa" and whatsapp is not None:
            # The bot pushes into the CRM, so its evidence is handovers rather than a saved account.
            if whatsapp["receiving"]:
                status, tone, source = "Receiving handovers", "good", "cms"
            elif whatsapp["total"]:
                status, tone, source = "No recent handovers", "warn", "cms"
            else:
                status, tone = "Waiting for first handover", "warn"
        result.append({**definition, "status": status, "tone": tone,
                       "source": source, "requirements": requirements,
                       "feeds": feeds, "sync_supported": platform in SYNC_PLATFORMS,
                       "account_count": len(matches), "receiving_count": len(healthy)})
    return result


def tracking_plan(ga4_events: dict | None = None) -> list[dict]:
    # A GA4 event count is the only delivery evidence; it says nothing about Meta or TikTok.
    if ga4_events is None:
        return [{**event, "status": "Unverified", "source": "plan"} for event in GTM_EVENTS]
    result = []
    for event in GTM_EVENTS:
        count = ga4_events.get(event["event"], 0)
        status = "Receiving" if count else "Not seen in GA4"
        result.append({**event, "status": status, "count": count, "source": "connector" if count else "plan"})
    return result
