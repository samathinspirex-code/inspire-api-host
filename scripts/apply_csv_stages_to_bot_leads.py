"""Give WhatsApp bot leads still at New Lead their real stage from the Zoho CSV export.

The bot backfill on 15 Nov 2026 created leads as New Lead when it could not match
an existing lead (it only compared the phone column). Many of those people are in
the 1 Nov Zoho export with the correct stage. For each bot lead still at New Lead
that no counsellor has moved:

* Case A: the CSV lead is also in the CRM (by its Zoho Record Id) -> the bot lead is a
  duplicate; it is merged into the CSV lead, which keeps its stage and counsellor.
* Case B: no separate CSV lead -> the bot lead gets the CSV stage, with a note.

Numbers are compared on their last 9 digits across phone and WhatsApp (CRM) and
Phone, Phone 1, Mobile and a number-like name (CSV).

    python scripts/apply_csv_stages_to_bot_leads.py CSV            preview only
    python scripts/apply_csv_stages_to_bot_leads.py CSV --apply    write
"""

import argparse
import asyncio
import csv
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.database import engine  # noqa: E402
from merge_duplicate_crm_leads import lead_columns, merge_into  # noqa: E402

STAGES = {
    "new lead": "new_lead", "new leads": "new_lead", "new inquiry": "new_lead",
    "uncontactable": "uncontactable", "contacted": "contacted", "follow up": "follow_up",
    "potential": "potential", "future prospect": "future_prospect",
    "not interested": "not_interested", "enrolled": "enrolled",
}
LABELS = {"new_lead": "New Lead", "uncontactable": "Uncontactable", "contacted": "Contacted", "follow_up": "Follow Up",
          "potential": "Potential", "future_prospect": "Future Prospect", "not_interested": "Not Interested",
          "enrolled": "Enrolled"}
TARGETS = """
    SELECT l.lead_id, l.phone, l.whatsapp FROM crm_leads l
    WHERE l.is_archived = false AND l.stage = 'new_lead'
      AND (l.external_record_id LIKE 'wabot:%%'
           OR EXISTS (SELECT 1 FROM wabot_lead_state s WHERE s.crm_lead_id = l.lead_id))
      AND NOT EXISTS (SELECT 1 FROM crm_activities a WHERE a.lead_id = l.lead_id AND a.activity_type = 'stage_change')
    ORDER BY l.lead_id
"""


def words(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def key9(value) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-9:] if len(digits) >= 9 else None


def when(value) -> datetime | None:
    try:
        return datetime.strptime(str(value or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def read_csv(path: Path) -> tuple[dict[str, dict], Counter]:
    """last-9-digit number -> latest CSV row (by Modified Time), plus unknown stage counts."""
    by_number: dict[str, dict] = {}
    unknown: Counter = Counter()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            stage = STAGES.get(words(row.get("Stage")))
            if stage is None:
                unknown[row.get("Stage") or "(blank)"] += 1
                continue
            item = {"record_id": (row.get("Record Id") or "").strip(), "stage": stage, "raw_stage": row.get("Stage"),
                    "modified": when(row.get("Modified Time")) or datetime.min, "changed": when(row.get("Change Log Time")),
                    "closing": when(row.get("Closing Date")) or when(f"{row.get('Closing Date') or ''} 09:00:00"),
                    "reason": (row.get("Reason For Loss") or "").strip() or None}
            name = row.get("Students Pipeline Name") or ""
            sources = [row.get("Phone"), row.get("Phone 1"), row.get("Mobile")]
            if re.fullmatch(r"[+\d\s()\-./]+", name.strip() or "x"):
                sources.append(name)
            for number in {key9(v) for v in sources} - {None}:
                current = by_number.get(number)
                if current is None or item["modified"] >= current["modified"]:
                    by_number[number] = item
    return by_number, unknown


async def main(path: Path, apply: bool) -> None:
    engine.echo = False
    csv_rows, unknown = read_csv(path)
    print(f"CSV numbers with a known stage: {len(csv_rows):,}")
    if unknown:
        print("Skipped unknown CSV stages:", unknown.most_common())
    async with engine.connect() as conn:
        targets = (await conn.execute(text(TARGETS))).mappings().all()
        zoho = {r[0]: r[1] for r in (await conn.execute(text(
            # CSV Record Ids come in several forms (zcrm_…, recorrected-…-row-N, <name>-row-N); bot ids are wabot:…
            "SELECT external_record_id, lead_id FROM crm_leads WHERE is_archived = false "
            "AND external_record_id IS NOT NULL AND external_record_id NOT LIKE 'wabot:%'"))).all()}
        columns, links = await lead_columns(conn)
    print(f"Bot leads still at New Lead, never moved by a counsellor: {len(targets):,}")

    plan, counts, stages = [], Counter(), Counter()
    for lead in targets:
        match = next((csv_rows[k] for k in (key9(lead["phone"]), key9(lead["whatsapp"])) if k and k in csv_rows), None)
        if match is None:
            counts["not in the CSV (stay New Lead)"] += 1
            continue
        csv_lead = zoho.get(match["record_id"])
        if csv_lead and csv_lead != lead["lead_id"]:
            plan.append(("merge", lead["lead_id"], csv_lead, match))
            counts["A: duplicate of the CSV lead -> merge"] += 1
        elif match["stage"] == "new_lead":
            counts["CSV also says New Lead (no change)"] += 1
        else:
            plan.append(("stage", lead["lead_id"], None, match))
            counts["B: set stage from the CSV"] += 1
            stages[LABELS[match["stage"]]] += 1
    for label, n in sorted(counts.items()):
        print(f"  {label}: {n:,}")
    if stages:
        print("  Case B stages:", ", ".join(f"{k} {v}" for k, v in stages.most_common()))
    for kind, lead_id, csv_lead, match in plan[:10]:
        print(f"  e.g. #{lead_id}: " + (f"merge into #{csv_lead} ({match['record_id']})" if kind == "merge"
                                         else f"stage -> {LABELS[match['stage']]} ({match['record_id']})"))
    if not apply:
        print("Preview only; add --apply to write.")
        await engine.dispose()
        return

    done = Counter()
    for kind, lead_id, csv_lead, match in plan:
        async with engine.begin() as conn:
            rows = {r["lead_id"]: dict(r) for r in (await conn.execute(text(
                "SELECT * FROM crm_leads WHERE lead_id = ANY(:ids) AND is_archived = false FOR UPDATE"),
                {"ids": [i for i in (lead_id, csv_lead) if i]})).mappings()}
            bot = rows.get(lead_id)
            if bot is None or bot["stage"] != "new_lead":
                continue  # changed since the preview
            if kind == "merge":
                keep = rows.get(csv_lead)
                if keep is None:
                    continue
                await merge_into(conn, keep, [bot], columns, links)
                done["merged"] += 1
                continue
            stage = match["stage"]
            values = {"id": lead_id, "stage": stage,
                      "enrolled_at": match["changed"] if stage == "enrolled" else None,
                      "followup": match["closing"] if stage == "follow_up" else None}
            await conn.execute(text(
                "UPDATE crm_leads SET stage = :stage, "
                "enrolled_at = coalesce(enrolled_at, :enrolled_at), "
                "followup_date = coalesce(followup_date, :followup), updated_at = now() WHERE lead_id = :id"), values)
            note = f"Stage set to {LABELS[stage]} from the 1 Nov CRM export ({match['record_id']}); it was New Lead after the WhatsApp bot import."
            if stage == "not_interested" and match["reason"]:
                note += f" Reason for loss: {match['reason']}."
            await conn.execute(text("INSERT INTO crm_activities (lead_id, activity_type, content) VALUES (:id, 'note', :c)"),
                               {"id": lead_id, "c": note})
            done["stage set"] += 1
    print("Applied:", dict(done))
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.csv, args.apply))
