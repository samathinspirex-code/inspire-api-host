"""Shared, honest readiness for the entire Growth & Data source catalogue."""
from datetime import datetime, timezone

from app.modules.insights.demo_data import GTM_EVENTS, PLATFORMS
from app.modules.insights.website import REPORT_FRESH_FOR

# A saved ID, a configured credential and an ingested report are different things.
REQUIREMENTS = {
    "meta": ("Ad account ID and a Meta System User token with only ads_read", "Paid campaigns, spend and leads"),
    "capi": ("Pixel ID, a Conversions API token and compliance approval. Sends hashed phone and email to Meta, only for leads with recorded consent", "Lead and enrolment feedback"),
    "ga4": ("Numeric GA4 property ID and Google analytics.readonly consent", "Website sessions and engagement"),
    "gtm": ("GTM container ID (GTM-XXXX) published on the website. No Google login is needed for this check", "Installation check and duplicate GA4 tag warnings"),
    "gads": ("Customer ID, developer token and Google Ads consent", "Search campaigns and offline conversions"),
    "gsc": ("Verified site URL or sc-domain property and webmasters.readonly consent", "Organic search clicks and impressions"),
    "tiktok": ("Advertiser ID and approved TikTok Business app", "Paid video campaigns and lead forms"),
    "wa": ("The bot's Supabase URL and service key in the API env (WABOT_SUPABASE_URL, WABOT_SUPABASE_SERVICE_ROLE_KEY), the inspire-wabot-sync timer, and bot counsellor emails matching CRM users", "CRM -> WhatsApp Bot section, bot handovers pulled into CRM leads, chat timelines and logged reply times"),
    "ig": ("Professional Instagram account and approved insights permissions", "Organic content performance"),
    "li": ("Ad account ID and approved LinkedIn Marketing API access", "Professional audience campaigns"),
    "yt": ("Channel ID and Google yt-analytics.readonly consent", "Video views and watch time"),
    "email": ("Built in. Uses the Mailjet account already set on the server. There is no SMS provider yet", "Sent, blocked, bounced, opened and clicked emails"),
    "payhere": ("Built in. Uses the Payments ledger and the PayHere merchant set on the server", "Outstanding fees, overdue invoices and on-time payments"),
    "brain": ("Built in. Reads attendance, enrolments and learning activity from the LMS", "Attendance, dropout and retention by intake"),
    "forms": ("Existing website form submission route", "Enquiries into the CRM"),
    "calls": ("Choose a telephony provider and configure signed call webhooks", "Calls, outcomes and durations"),
}
SYNC_PLATFORMS = {"ga4", "gsc", "yt", "gtm", "meta", "email"}


def account_state(account: dict, configured: bool, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    synced = account.get("last_synced_at")
    if isinstance(synced, str):
        synced = datetime.fromisoformat(synced)
    if synced and synced.tzinfo is None:
        synced = synced.replace(tzinfo=timezone.utc)
    stale = bool(synced and now - synced > REPORT_FRESH_FOR)
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


def _capi_state(capi: dict) -> tuple[str, str, str]:
    if not capi["configured"]:
        return "Not connected", "", "unavailable"
    if not capi["installed"]:
        return "Setup needed", "warn", "unavailable"
    if not capi["enabled"]:
        return "Ready · sending off", "warn", "unavailable"
    if capi["failed_30d"] or capi["last_error"]:
        return "Delivery problem", "bad", "connector"
    return ("Sending events", "good", "connector") if capi["sent_30d"] else ("Waiting for events", "warn", "connector")


def platforms_for(accounts: list[dict], whatsapp: dict | None = None, lms: dict | None = None, finance: dict | None = None, capi: dict | None = None) -> list[dict]:
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
            # The API pulls handovers from the bot's Supabase; evidence is the pull plus CRM handovers.
            if not whatsapp.get("configured"):
                status, tone = "Bot DB not configured", "warn"
            elif whatsapp.get("last_error"):
                status, tone = "Bot DB unreachable", "bad"
            elif whatsapp["receiving"]:
                status, tone, source = "Syncing handovers", "good", "cms"
            elif whatsapp["total"]:
                status, tone, source = "No recent handovers", "warn", "cms"
            else:
                status, tone = "Waiting for first handover", "warn"
        if platform == "brain" and lms is not None and lms["connected"]:
            status, tone, source = ("Receiving data", "good", "cms") if lms["receiving"] else ("No recent activity", "warn", "cms")
        if platform == "payhere" and finance is not None and finance["live"]:
            if finance["mode"] == "live":
                status, tone, source = ("Receiving data" if finance["payments"] else "Connected · live"), "good", "cms"
            else:
                status, tone, source = f"Ledger linked · {finance['mode']}", "warn", "cms"
        if platform == "capi" and capi is not None:
            status, tone, source = _capi_state(capi)
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
