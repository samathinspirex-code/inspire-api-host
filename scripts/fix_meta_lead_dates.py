"""Set "Added" on Meta lead-form leads to when the student submitted the form.

Before 15 Nov 2026, intake stored the time it picked a lead up. After the backfill
that day, about 950 older leads showed 15 Nov. This asks Meta for each lead's
created_time (by its Meta lead ID) and corrects created_at. Nothing else changes.

    python scripts/fix_meta_lead_dates.py            preview only
    python scripts/fix_meta_lead_dates.py --apply    write the corrected dates

Needs CONNECTORS_META_LEADS_TOKEN and CONNECTORS_META_PAGE_ID (the server's .env).
Meta keeps lead details for about 90 days; leads it no longer has are left as they are.
"""

import argparse
import asyncio
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.modules.connectors.providers import ProviderFailure, _meta_get  # noqa: E402
from app.modules.crm.meta_leads import SOURCE, _page_token, is_configured, meta_time  # noqa: E402

SAME = timedelta(minutes=2)


async def main(apply: bool) -> int:
    engine.echo = False
    if not is_configured():
        print("Meta lead intake is not configured here (token / Page ID missing). Run this on the server.")
        return 1
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT lead_id, social_lead_id, created_at FROM crm_leads "
            "WHERE source = :s AND social_lead_id IS NOT NULL ORDER BY lead_id"
        ), {"s": SOURCE})).all()
    print(f"Meta lead-form leads with a Meta lead ID: {len(rows)}")
    fixes, counts = [], Counter()
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        token = await _page_token(client, settings.CONNECTORS_META_LEADS_TOKEN, settings.CONNECTORS_META_PAGE_ID)
        for lead_id, meta_id, created_at in rows:
            try:
                data = await _meta_get(client, str(meta_id), token, {"fields": "created_time"})
            except ProviderFailure as exc:
                if "rate limit" in str(exc).lower():
                    print("Meta rate limit reached; stopping. Run again later; finished leads are skipped.")
                    break
                counts["not available from Meta"] += 1
                continue
            submitted = meta_time(data.get("created_time"))
            if submitted is None:
                counts["no time from Meta"] += 1
                continue
            submitted = submitted.replace(tzinfo=None)  # live CRM leads store naive UTC
            if abs(submitted - created_at) <= SAME:
                counts["already right"] += 1
                continue
            fixes.append((lead_id, submitted))
            counts["to fix"] += 1
    for name, n in counts.items():
        print(f"  {name}: {n}")
    for lead_id, submitted in fixes[:5]:
        print(f"  e.g. lead {lead_id} -> {submitted:%Y-%m-%d %H:%M} UTC")
    if not apply:
        print("Preview only; add --apply to write.")
        return 0
    async with AsyncSessionLocal() as db:
        for lead_id, submitted in fixes:
            # assigned_at follows only when it was stamped with the same wrong pickup time.
            await db.execute(text(
                "UPDATE crm_leads SET assigned_at = CASE WHEN assigned_at IS NOT NULL AND abs(extract(epoch FROM assigned_at - created_at)) < 120 "
                "THEN :t ELSE assigned_at END, created_at = :t WHERE lead_id = :id AND source = :s"
            ), {"t": submitted, "id": lead_id, "s": SOURCE})
        await db.commit()
    print(f"Updated {len(fixes)} leads.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    async def run() -> int:
        try:
            return await main(args.apply)
        finally:
            await engine.dispose()

    sys.exit(asyncio.run(run()))
