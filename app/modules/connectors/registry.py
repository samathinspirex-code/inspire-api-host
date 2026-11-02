"""Shared, honest readiness for the entire Growth & Data source catalogue."""
from datetime import datetime, timedelta, timezone

from app.modules.insights.demo_data import GTM_EVENTS, PLATFORMS

# A saved ID, a configured credential and an ingested report are different things.
REQUIREMENTS = {
    "meta": ("Ad account ID and Meta app approval with ads_read", "Paid campaigns, spend and attribution"),
    "capi": ("Dataset/pixel ID, server token and consent-aware event outbox", "Lead and enrolment feedback"),
    "ga4": ("Numeric GA4 property ID and Google analytics.readonly consent", "Website sessions and engagement"),
    "gtm": ("GTM container ID, website installation and publish approval", "Website event delivery"),
    "gads": ("Customer ID, developer token and Google Ads consent", "Search campaigns and offline conversions"),
    "gsc": ("Verified site URL or sc-domain property and webmasters.readonly consent", "Organic search clicks and impressions"),
    "tiktok": ("Advertiser ID and approved TikTok Business app", "Paid video campaigns and lead forms"),
    "wa": ("Business account, verified phone IDs, approved app and signed webhooks", "Conversations, routing and reply times"),
    "ig": ("Professional Instagram account and approved insights permissions", "Organic content performance"),
    "li": ("Ad account ID and approved LinkedIn Marketing API access", "Professional audience campaigns"),
    "yt": ("Channel ID and Google yt-analytics.readonly consent", "Video views and watch time"),
    "email": ("Choose Email and SMS providers and configure delivery webhooks", "Campaign delivery and engagement"),
    "payhere": ("Existing Payments configuration and verified payment notifications", "Fees and payment reconciliation"),
    "brain": ("Map existing LMS records to attendance and retention definitions", "Student activity and retention"),
    "forms": ("Existing website form submission route", "Enquiries into the CRM"),
    "calls": ("Choose a telephony provider and configure signed call webhooks", "Calls, outcomes and durations"),
}
SYNC_PLATFORMS = {"ga4", "gsc", "yt"}


def account_state(account: dict, configured: bool, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    synced = account.get("last_synced_at")
    if isinstance(synced, str):
        synced = datetime.fromisoformat(synced)
    if synced and synced.tzinfo is None:
        synced = synced.replace(tzinfo=timezone.utc)
    stale = bool(synced and now - synced > timedelta(hours=48))
    if account.get("last_error"):
        label, tone = "Sync failed", "bad"
    elif synced:
        label, tone = ("Stale", "warn") if stale else ("Receiving data", "good")
    elif configured and account["platform"] in SYNC_PLATFORMS:
        label, tone = "Ready to sync", "warn"
    elif account["platform"] in SYNC_PLATFORMS:
        label, tone = "Credentials needed", "warn"
    else:
        label, tone = "Integration pending", "warn"
    return {"status_label": label, "tone": tone, "stale": stale, "can_sync": configured and account["platform"] in SYNC_PLATFORMS}


def platforms_for(accounts: list[dict]) -> list[dict]:
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
        requirements, feeds = REQUIREMENTS[platform]
        result.append({**definition, "status": status, "tone": tone,
                       "source": "connector" if healthy else "unavailable", "requirements": requirements,
                       "feeds": feeds, "sync_supported": platform in SYNC_PLATFORMS,
                       "account_count": len(matches), "receiving_count": len(healthy)})
    return result


def tracking_plan() -> list[dict]:
    # No runtime delivery evidence has been wired up yet; sample Live badges are unsafe.
    return [{**event, "status": "Unverified", "source": "plan"} for event in GTM_EVENTS]
