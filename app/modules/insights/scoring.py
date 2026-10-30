"""Lead score, win probability and next best action from the Admissions Studio model."""

from datetime import datetime

STAGE_BASE = {
    "new_lead": 16,
    "uncontactable": 4,
    "contacted": 30,
    "future_prospect": 22,
    "not_interested": 3,
    "lost_to_competitor": 0,
    "cant_afford": 14,
    "enrolled": 40,
}
STAGE_WIN = {
    "new_lead": 0.06,
    "uncontactable": 0.02,
    "contacted": 0.20,
    "future_prospect": 0.10,
    "not_interested": 0.01,
    "lost_to_competitor": 0.0,
    "cant_afford": 0.04,
    "enrolled": 1.0,
}
SOURCE_QUALITY = {
    "meta": 2,
    "meta_lead_forms": 2,
    "facebook_chat": 2,
    "instagram": 2,
    "whatsapp": 5,
    "website_admission": 8,
    "website_contact": 6,
    "wtc_registrations_form": 6,
    "manual": 4,
    "referral": 12,
    "walk_in": 11,
    "fair": 6,
    "hotline_call": 4,
    "tiktok_lead_forms": 3,
    "tiktok_chat": 3,
    "linkedin": 4,
}
CLOSED = {"enrolled", "not_interested", "lost_to_competitor"}
SOURCE_LABELS = {
    "website_admission": "Admission form",
    "website_contact": "Contact form",
    "whatsapp": "WhatsApp",
    "meta": "Meta Ads",
    "meta_lead_forms": "Meta Lead Forms",
    "facebook_chat": "Facebook Chat",
    "instagram": "Instagram",
    "tiktok_chat": "TikTok Chat",
    "tiktok_lead_forms": "TikTok Lead Forms",
    "tiktok": "TikTok Lead Forms",
    "linkedin": "LinkedIn",
    "referral": "Referral",
    "fair": "Education Fair",
    "walk_in": "Walk-in",
    "hotline_call": "Direct Calls",
    "manual": "Manual",
    "wtc_registrations_form": "WTC registration form",
}


def source_label(key: str | None) -> str:
    if not key:
        return "Manual"
    return SOURCE_LABELS.get(key, key.replace("_", " ").title())


def hours_since(moment: datetime | None, now: datetime) -> float | None:
    if moment is None:
        return None
    value = moment.replace(tzinfo=None) if moment.tzinfo else moment
    return max(0.0, (now - value).total_seconds() / 3600)


def factors(lead: dict, now: datetime) -> list[dict]:
    stage = lead.get("stage") or "new_lead"
    base = STAGE_BASE.get(stage, 10)
    touches = int(lead.get("followup_count") or 0)
    engagement = min(33, touches * 5 + (8 if touches else 0) + (5 if lead.get("instalment") else 0))
    last = hours_since(lead.get("last_touch"), now)
    if last is None:
        last = hours_since(lead.get("created_at"), now) or 9999
    recency = 12 if last < 24 else 7 if last < 72 else 2 if last < 168 else 0
    quality = SOURCE_QUALITY.get(lead.get("source") or "", 3)
    instalment = 5 if lead.get("instalment") else 0
    return [
        {"name": "Stage signal", "value": base, "max": 40},
        {"name": "Engagement", "value": engagement, "max": 33},
        {"name": "Recency of last touch", "value": recency, "max": 12},
        {"name": f"Source quality · {source_label(lead.get('source'))}", "value": quality, "max": 12},
        {"name": "Can pay by instalment", "value": instalment, "max": 5},
    ]


def score_lead(lead: dict, now: datetime) -> int:
    stage = lead.get("stage")
    if stage == "enrolled":
        return 100
    if stage == "lost_to_competitor":
        return 2
    total = sum(item["value"] for item in factors(lead, now))
    return max(1, min(99, total))


def win_probability(lead: dict, now: datetime) -> float:
    stage = lead.get("stage") or "new_lead"
    if stage == "enrolled":
        return 1.0
    base = STAGE_WIN.get(stage, 0.05)
    return min(0.95, base * (0.45 + score_lead(lead, now) / 90))


def heat(score: int) -> str:
    return "hot" if score >= 62 else "warm" if score >= 40 else "cold"


def next_action(lead: dict, now: datetime) -> str:
    stage = lead.get("stage") or "new_lead"
    last = hours_since(lead.get("last_touch") or lead.get("created_at"), now) or 0
    attempts = int(lead.get("followup_count") or 0)
    if stage == "new_lead":
        if last > 1:
            return "Call now. This lead has been waiting and new enquiries cool quickly after 24 hours."
        return "Call within 15 minutes for the speed-to-lead bonus."
    if stage == "uncontactable":
        if attempts >= 3:
            return "Switch channel: send a WhatsApp voice note, then try 7–9 pm."
        return "Retry in the 7–9 pm window, when this segment answers most."
    if stage == "contacted":
        return "Book a 20-minute counselling call and send the programme brochure."
    if stage == "future_prospect":
        return "Add them to the next intake nurture list and follow up in 14 days with a graduate story."
    if stage == "cant_afford":
        return "Offer the monthly instalment plan, or an employer-sponsorship letter."
    if stage == "not_interested":
        return "Ask one reason-for-no question, then archive. It feeds the objection report."
    if stage == "lost_to_competitor":
        return "Log the competitor and price point. No further outreach."
    return "Send the welcome pack and ask for one referral."
