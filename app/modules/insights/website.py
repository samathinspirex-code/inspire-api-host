"""Turns the latest saved GA4 report into the shape the visitor panels use."""
import re
from datetime import datetime, timedelta, timezone

from app.modules.insights.demo_data import PAGES, VISITORS

HOME_COUNTRY = "Sri Lanka"
REPORT_FRESH_FOR = timedelta(days=8)  # Reports sync weekly; a day's grace before one counts as stale.
UNKNOWN = {"(not set)", ""}
CITY_COORDS = {
    "Colombo": (6.93, 79.85), "Gampaha": (7.09, 79.99), "Kandy": (7.29, 80.63), "Kurunegala": (7.49, 80.36), "Negombo": (7.21, 79.84),
    "Galle": (6.05, 80.22), "Matara": (5.95, 80.55), "Ratnapura": (6.69, 80.40), "Jaffna": (9.66, 80.02), "Anuradhapura": (8.31, 80.40),
    "Batticaloa": (7.73, 81.69), "Trincomalee": (8.59, 81.21), "Sri Jayawardenepura Kotte": (6.89, 79.90), "Dehiwala": (6.85, 79.87),
    "Moratuwa": (6.77, 79.88), "Kalutara": (6.58, 79.96), "Badulla": (6.99, 81.06), "Nuwara": (6.97, 80.78), "Kegalle": (7.25, 80.35),
    "Matale": (7.47, 80.62), "Hambantota": (6.12, 81.12), "Puttalam": (8.04, 79.83), "Vavuniya": (8.75, 80.50), "Polonnaruwa": (7.94, 81.0),
    "Ampara": (7.29, 81.67), "Chilaw": (7.58, 79.80), "Panadura": (6.71, 79.90), "Kaduwela": (6.93, 79.98), "Maharagama": (6.85, 79.93),
    "Kelaniya": (6.95, 79.92), "Dehiwala-Mount Lavinia": (6.85, 79.87), "Malabe": (6.90, 79.96), "Kadawatha": (7.00, 79.95),
    "Katunayake": (7.17, 79.88), "Battaramulla": (6.90, 79.92),
}


def _clock(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0


def _fresh(account: dict) -> bool:
    synced = datetime.fromisoformat(account["last_synced_at"])
    synced = synced if synced.tzinfo else synced.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - synced <= REPORT_FRESH_FOR


def path_persona(path: str) -> tuple[int, int]:
    """(student %, parent %) inferred from a page's topic; the rest are professionals. GA4 does not measure this."""
    page = path.lower()
    if re.search(r"fee|payment|scholar|accredit|about|safety|campus|recogn|parent", page):
        return 20, 62
    if re.search(r"cpd|ai-for|finance|digital-market|post ?grad|postgrad|pg|master|scu|level-7|mba|executive", page):
        return 12, 14
    if re.search(r"hnd|foundation|student|career|course|program", page):
        return 60, 16
    return 40, 25


def page_intent(path: str) -> str:
    page = path.lower()
    if re.search(r"admission|apply|enrol", page):
        return "Ready to apply"
    if re.search(r"fee|payment|scholar", page):
        return "Checking fees"
    if re.search(r"program|course|hnd|cpd|master|mba", page):
        return "Comparing programmes"
    return "Browsing"


def latest_report(accounts: list[dict], platform: str) -> dict | None:
    # One account's report at a time: users cannot be summed across properties.
    matches = [a for a in accounts if a["platform"] == platform and a.get("report") and a.get("last_synced_at")]
    return max(matches, key=lambda a: a["last_synced_at"], default=None)


def ga4_events(accounts: list[dict]) -> dict | None:
    account = latest_report(accounts, "ga4")
    if not account or "breakdowns" not in account["report"]:
        return None
    return {row["name"]: row["count"] for row in account["report"]["breakdowns"].get("events", [])}


def _city_rows(cities: list[dict], city_pages: list[dict]) -> list[dict]:
    mix: dict[str, list[float]] = {}
    for row in city_pages:
        student, parent = path_persona(row["path"])
        entry = mix.setdefault(row["city"], [0, 0, 0])
        entry[0] += row["views"] * student
        entry[1] += row["views"] * parent
        entry[2] += row["views"] * (100 - student - parent)
    rows = []
    for row in cities:
        if row["city"] in UNKNOWN:
            continue
        weights = mix.get(row["city"]) or [1, 1, 1]
        total = sum(weights) or 1
        coords = CITY_COORDS.get(row["city"]) if row["country"] == HOME_COUNTRY else None
        rows.append({"city": row["city"], "country": row["country"], "users": row["users"],
                     "lat": coords[0] if coords else None, "lon": coords[1] if coords else None,
                     "s": row["users"] * weights[0] / total, "p": row["users"] * weights[1] / total, "w": row["users"] * weights[2] / total})
    return sorted(rows, key=lambda item: -item["users"])


def visitors_view(account: dict | None) -> dict | None:
    if not account:
        return None
    report = account["report"]
    totals = report["metrics"]
    parts = report.get("breakdowns") or {}
    countries = parts.get("countries", [])
    known = [row for row in countries if row["name"] not in UNKNOWN]
    located = sum(row["sessions"] for row in known)
    overseas = sum(row["sessions"] for row in known if row["name"] != HOME_COUNTRY)
    devices = parts.get("devices", [])
    device_sessions = sum(row["sessions"] for row in devices)
    device_seconds = sum(row.get("engagement_seconds", 0) for row in devices)
    mobile = sum(row["sessions"] for row in devices if row["name"] == "mobile")
    leads = next((row["count"] for row in parts.get("events", []) if row["name"] == "generate_lead"), 0)
    pages = parts.get("pages", [])
    persona = [0.0, 0.0, 0.0]
    intent: dict[str, float] = {}
    for row in pages:
        student, parent = path_persona(row["path"])
        persona[0] += row["views"] * student
        persona[1] += row["views"] * parent
        persona[2] += row["views"] * (100 - student - parent)
        intent[page_intent(row["path"])] = intent.get(page_intent(row["path"]), 0) + row["views"]
    hours = [0.0] * 24
    for row in parts.get("hours", []):
        if str(row["hour"]).isdigit() and int(row["hour"]) < 24:
            hours[int(row["hour"])] = row["sessions"]
    sessions = totals.get("sessions", 0)
    return {
        "live": True,
        "account": account["name"],
        "start": report["start"],
        "end": report["end"],
        "last_synced_at": account["last_synced_at"],
        "fresh": _fresh(account),
        "has_breakdowns": bool(parts),
        "period": "30 days",
        "users": totals.get("activeUsers", 0),
        "sessions": sessions,
        "page_views": totals.get("screenPageViews", 0),
        "engagement_rate": _pct(totals.get("engagedSessions", 0), sessions),
        "avg_engagement": _clock(device_seconds / device_sessions) if device_sessions else "—",
        "overseas": _pct(overseas, located),
        "mobile": _pct(mobile, device_sessions),
        "generate_lead": leads,
        "session_to_lead": _pct(leads, sessions),
        "personas": [{"name": name, "value": value} for name, value in zip(("Students", "Parents & guardians", "Working professionals"), persona)],
        "countries": [{"name": row["name"], "value": row["sessions"]} for row in countries[:6]],
        "country_rows": [{"name": row["name"], "value": row["sessions"]} for row in known[:8]],
        "sources": [{"name": row["name"], "value": row["sessions"]} for row in parts.get("channels", [])[:7]],
        "devices": [{"name": row["name"].title(), "value": row["sessions"]} for row in devices],
        "visitor_types": [{"name": f"{row['name'].title()} visitors" if row["name"] == "new" else row["name"].title(), "value": row["users"]}
                          for row in parts.get("visitor_types", []) if row["name"] in {"new", "returning"}],
        "intent": [{"name": name, "value": intent.get(name, 0)} for name in ("Browsing", "Comparing programmes", "Checking fees", "Ready to apply")],
        "hours": hours,
        "cities": _city_rows(parts.get("cities", []), parts.get("city_pages", [])),
        "pages": [{"path": row["path"], "views": row["views"], "users": row["users"],
                   "time": _clock(row["engagement_seconds"] / row["views"]) if row["views"] else "0:00",
                   "student": path_persona(row["path"])[0], "parent": path_persona(row["path"])[1]}
                  for row in pages],
        "reporting_note": report.get("reporting_note"),
    }


def search_view(account: dict | None) -> dict | None:
    if not account:
        return None
    report = account["report"]
    totals = report["metrics"]
    parts = report.get("breakdowns") or {}
    pages = [{**row, "name": re.sub(r"^https?://[^/]+", "", row["name"]) or "/"} for row in parts.get("pages", [])]
    return {"account": account["account_id"], "start": report["start"], "end": report["end"], "fresh": _fresh(account),
            "clicks": totals.get("clicks", 0), "impressions": totals.get("impressions", 0),
            "ctr": round(totals.get("ctr", 0) * 100, 2), "position": round(totals.get("position", 0), 1),
            "queries": parts.get("queries", []), "pages": pages}


def demo_visitors() -> dict:
    """The design's sample mix, in the same shape as a live report. Always tagged demo in the UI."""
    return {**VISITORS, "live": False, "has_breakdowns": True, "period": "7 days", "pages": PAGES}
