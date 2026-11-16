"""Merge CRM leads that share a phone number into one lead.

    python scripts/merge_duplicate_crm_leads.py           preview only
    python scripts/merge_duplicate_crm_leads.py --apply   merge

For each phone number (last 9 digits) on more than one open lead:
* The lead kept is the one furthest along (Enrolled > Future Prospect > Potential /
  Follow Up > Contacted > Not Interested > Uncontactable > New Lead); a tie keeps the oldest.
* Empty fields on the kept lead are filled from the others; nothing on it is overwritten.
* "Added" becomes the earliest date in the group (first contact).
* Activities, Meta and WhatsApp-bot links move to the kept lead, so syncs update it.
* The others are archived, not deleted, with a note on the kept lead naming them.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import engine  # noqa: E402

RANK = {"enrolled": 7, "future_prospect": 6, "potential": 5, "follow_up": 5, "contacted": 4,
        "not_interested": 3, "uncontactable": 2, "new_lead": 1}
NEVER_COPY = {"lead_id", "created_at", "updated_at", "is_archived", "external_record_id", "social_lead_id", "stage",
              "status_reason", "status_remarks"}  # the outcome belongs with the kept stage
LINKS = [("crm_activities", "lead_id"), ("meta_capi_consents", "lead_id"), ("meta_capi_events", "lead_id"),
         ("wabot_lead_state", "crm_lead_id"), ("wabot_sync_state", "crm_lead_id")]
GROUPS = """
    WITH p AS (SELECT lead_id, right(regexp_replace(coalesce(phone, ''), '[^0-9]', '', 'g'), 9) AS k
               FROM crm_leads WHERE is_archived = false)
    SELECT k, array_agg(lead_id ORDER BY lead_id) AS ids FROM p WHERE length(k) = 9 GROUP BY k HAVING count(*) > 1
"""


def empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


async def lead_columns(conn) -> tuple[list[str], list[tuple[str, str]]]:
    columns = [r[0] for r in (await conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='crm_leads'"))).all()]
    links = [(t, c) for t, c in LINKS if (await conn.scalar(text("SELECT to_regclass(:t)"), {"t": t}))]
    return columns, links


async def merge_into(conn, keep: dict, others: list[dict], columns: list[str], links: list[tuple[str, str]]) -> None:
    """Fold `others` into `keep` (rows as dicts): fill empty fields, move links, archive the others, add a note."""
    others = sorted(others, key=lambda l: l["created_at"])
    fill = {}
    for col in columns:
        if col in NEVER_COPY or not empty(keep.get(col)):
            continue
        donor = next((o[col] for o in others if not empty(o.get(col))), None)
        if donor is not None:
            fill[col] = donor
    for key in ("external_record_id", "social_lead_id"):
        if empty(keep.get(key)):
            donor = next((o for o in others if not empty(o.get(key))), None)
            if donor:
                await conn.execute(text(f"UPDATE crm_leads SET {key} = NULL WHERE lead_id = :id"), {"id": donor["lead_id"]})
                fill[key] = donor[key]
    fill["created_at"] = min(l["created_at"] for l in [keep, *others])
    sets = ", ".join(f'"{c}" = :{c}' for c in fill)
    await conn.execute(text(f"UPDATE crm_leads SET {sets}, updated_at = now() WHERE lead_id = :lead_id"),
                       {**fill, "lead_id": keep["lead_id"]})
    ids = [o["lead_id"] for o in others]
    for table, col in links:
        await conn.execute(text(f"UPDATE {table} SET {col} = :keep WHERE {col} = ANY(:ids)"), {"keep": keep["lead_id"], "ids": ids})
    await conn.execute(text("UPDATE crm_leads SET is_archived = true, updated_at = now() WHERE lead_id = ANY(:ids)"), {"ids": ids})
    note = "Merged duplicate lead(s) with the same phone: " + "; ".join(
        f"#{o['lead_id']} ({o['source']}, {o['stage']}, added {o['created_at']:%d %b %Y})" for o in others) + ". They are archived."
    await conn.execute(text("INSERT INTO crm_activities (lead_id, activity_type, content) VALUES (:id, 'note', :c)"),
                       {"id": keep["lead_id"], "c": note})


async def main(apply: bool) -> None:
    engine.echo = False
    async with engine.connect() as conn:
        groups = (await conn.execute(text(GROUPS))).all()
        columns, links = await lead_columns(conn)
    print(f"Phone numbers on more than one open lead: {len(groups)}; leads to archive: {sum(len(g.ids) - 1 for g in groups)}")
    for n, group in enumerate(groups):
        async with engine.begin() as conn:
            leads = [dict(r) for r in (await conn.execute(text(
                "SELECT * FROM crm_leads WHERE lead_id = ANY(:ids) AND is_archived = false FOR UPDATE"), {"ids": list(group.ids)})).mappings()]
            if len(leads) < 2:
                continue
            leads.sort(key=lambda l: (-RANK.get(l["stage"], 0), l["created_at"], l["lead_id"]))
            keep, others = leads[0], sorted(leads[1:], key=lambda l: l["created_at"])
            if n < 8 or not apply:
                if n < 8:
                    print(f"  keep #{keep['lead_id']} ({keep['source']}, {keep['stage']}) <- "
                          + ", ".join(f"#{o['lead_id']} ({o['source']}, {o['stage']})" for o in others))
            if not apply:
                continue
            await merge_into(conn, keep, others, columns, links)
    print("Merged." if apply else "Preview only; add --apply to merge.")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
