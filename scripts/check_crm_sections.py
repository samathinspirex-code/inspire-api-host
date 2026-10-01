"""Exercise every CRM read path and report latency plus failures."""
import asyncio
import time
from datetime import date, timedelta

from app.core.database import AsyncSessionLocal
from app.modules.crm import service


async def timed(label, coro_factory):
    async with AsyncSessionLocal() as db:
        started = time.perf_counter()
        try:
            result = await coro_factory(db)
            elapsed = (time.perf_counter() - started) * 1000
            print(f"OK   {label:<34} {elapsed:8.0f} ms  {summarize(result)}")
            return True
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            print(f"FAIL {label:<34} {elapsed:8.0f} ms  {type(exc).__name__}: {exc}")
            return False


def summarize(result):
    for attr in ("data", "stages", "counsellors", "rotation"):
        value = getattr(result, attr, None)
        if isinstance(value, list):
            return f"{attr}={len(value)}"
    if isinstance(result, list):
        return f"items={len(result)}"
    return ""


async def main() -> None:
    today = date.today()
    month_ago = today - timedelta(days=30)
    checks = [
        ("counsellors", lambda db: service.list_counsellors(db)),
        ("counsellors/roster", lambda db: service.list_counsellor_roster(db)),
        ("assignment-settings", lambda db: service.get_assignment_settings(db)),
        ("leads (page 1)", lambda db: service.list_leads(db, None, None, None, None, 1, 50, False)),
        ("leads (search)", lambda db: service.list_leads(db, None, None, None, "a", 1, 50, False)),
        ("leads (date range)", lambda db: service.list_leads(
            db, None, None, None, None, 1, 50, False, None, None, False,
            __import__("datetime").datetime.combine(month_ago, __import__("datetime").time.min),
            __import__("datetime").datetime.combine(today + timedelta(days=1), __import__("datetime").time.min),
        )),
        ("leads/filters", lambda db: service.list_lead_filters(db)),
        ("leads/pipeline", lambda db: service.get_pipeline(db)),
        ("leads/pipeline (filtered)", lambda db: service.get_pipeline(db, 20, None, None, None, True)),
        ("leads/dashboard", lambda db: service.get_dashboard(db)),
        ("reports (today)", lambda db: service.get_counsellor_report(db, today, today, None)),
        ("reports (30 days)", lambda db: service.get_counsellor_report(db, month_ago, today, None)),
    ]

    results = []
    for label, factory in checks:
        results.append(await timed(label, factory))

    failures = results.count(False)
    print(f"\n{len(results) - failures}/{len(results)} CRM sections OK")


if __name__ == "__main__":
    asyncio.run(main())
