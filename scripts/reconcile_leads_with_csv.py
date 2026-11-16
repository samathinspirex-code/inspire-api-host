"""Make every CRM lead found in the Zoho CSV match it: source, stage and counsellor.

The CSV export (e.g. Downloads/crm-leads-2026-11-01.csv) is the record of truth for
the leads it contains. A lead is matched by its Record Id (external_record_id), else
by phone number (last 9 digits of phone or WhatsApp against the CSV's Phone,
Phone 1, Mobile or a number-like name; the latest-modified CSV row wins).

* Source: the CSV Lead Source, as a CMS source key (WhatsApp Bot -> whatsapp, ...).
* Stage: the CSV Stage. Retired stages follow the CRM's own convention:
  Can't Afford / Lost to Competitor -> Not Interested with that reason; Contactable -> Contacted.
* Counsellor: the CSV owner, by first name, including paused former-staff accounts.
  Rows with no owner, or an owner without an account, leave the counsellor as it is.

Leads a counsellor has moved since the import (a stage_change activity) are skipped.
Each changed lead gets a note saying what changed.

    python scripts/reconcile_leads_with_csv.py CSV            preview only
    python scripts/reconcile_leads_with_csv.py CSV --apply    write
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

from app.core.database import engine  # noqa: E402

# CSV stage -> (stage, status_reason)
STAGES = {
    "new lead": ("new_lead", None), "new leads": ("new_lead", None), "new inquiry": ("new_lead", None),
    "uncontactable": ("uncontactable", None), "contacted": ("contacted", None), "contactable": ("contacted", None),
    "follow up": ("follow_up", None), "potential": ("potential", None), "future prospect": ("future_prospect", None),
    "not interested": ("not_interested", None), "enrolled": ("enrolled", None),
    "can t afford": ("not_interested", "Fees Beyond Expectations"),
    "lost to competitor": ("not_interested", "Joined Another Institute"),
}
# Import-style source keys -> the keys the CMS filters on (CMS-UI api/crmSources.ts).
SOURCES = {"whatsapp_bot": "whatsapp", "meta_ads": "meta", "tik_tok": "tiktok_lead_forms", "tiktok": "tiktok_lead_forms",
           "facebook_lead_forms": "meta_lead_forms", "direct_calls": "hotline_call", "manual_entry": "manual",
           "admission_form": "website_admission", "contact_form": "website_contact", "walk_in": "walk_in"}
LABELS = {"new_lead": "New Lead", "uncontactable": "Uncontactable", "contacted": "Contacted", "follow_up": "Follow Up",
          "potential": "Potential", "future_prospect": "Future Prospect", "not_interested": "Not Interested",
          "enrolled": "Enrolled"}


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


def source_key(value) -> str | None:
    raw = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return SOURCES.get(raw, raw) or None


def first_name(value) -> str | None:
    found = words(value).split()
    return {"jeniffer": "jennifer", "jenniffer": "jennifer"}.get(found[0], found[0]) if found else None


def read_csv(path: Path):
    by_id, by_number, unknown = {}, {}, Counter()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            mapped = STAGES.get(words(row.get("Stage")))
            if mapped is None:
                unknown[row.get("Stage") or "(blank)"] += 1
                continue
            item = {"record_id": (row.get("Record Id") or "").strip(), "stage": mapped[0], "reason": mapped[1],
                    "source": source_key(row.get("Lead Source")), "owner": first_name(row.get("Students Pipeline Owner")),
                    "modified": when(row.get("Modified Time")) or datetime.min, "changed": when(row.get("Change Log Time"))}
            if item["record_id"]:
                by_id[item["record_id"]] = item
            name = (row.get("Students Pipeline Name") or "").strip()
            sources = [row.get("Phone"), row.get("Phone 1"), row.get("Mobile")]
            if re.fullmatch(r"[+\d\s()\-./]+", name or "x"):
                sources.append(name)
            for number in {key9(v) for v in sources} - {None}:
                if number not in by_number or item["modified"] >= by_number[number]["modified"]:
                    by_number[number] = item
    return by_id, by_number, unknown


async def main(path: Path, apply: bool) -> None:
    engine.echo = False
    by_id, by_number, unknown = read_csv(path)
    print(f"CSV rows with a known stage: {len(by_id):,}" + (f"; skipped stages {unknown.most_common()}" if unknown else ""))
    async with engine.connect() as conn:
        leads = (await conn.execute(text(
            "SELECT l.lead_id, l.external_record_id, l.phone, l.whatsapp, l.source, l.stage, l.status_reason, "
            "l.assigned_counsellor_id, l.enrolled_at FROM crm_leads l WHERE l.is_archived = false "
            "AND NOT EXISTS (SELECT 1 FROM crm_activities a WHERE a.lead_id = l.lead_id AND a.activity_type = 'stage_change') "
            "ORDER BY l.lead_id"))).mappings().all()
        # The counsellors' November sheets are newer than the 1 Nov CSV: leads they updated keep the sheet values.
        from_sheets = set((await conn.execute(text(
            "SELECT DISTINCT lead_id FROM crm_activities WHERE content LIKE 'Counsellor sheet %' "
            "OR content LIKE 'Source set to Meta Ads: the counsellor sheet%'"))).scalars())
        people = (await conn.execute(text(
            "SELECT DISTINCT u.user_id, u.full_name FROM users u JOIN user_access_levels ual ON ual.user_id = u.user_id "
            "JOIN access_levels al ON al.access_level_id = ual.access_level_id "
            "WHERE u.is_active = true AND al.access_key = 'COUNSELLOR' ORDER BY u.user_id"))).all()
    counsellors = {}
    for user_id, name in people:
        counsellors.setdefault(first_name(name), (user_id, name))

    changes, counts, examples = [], Counter(), []
    for lead in leads:
        row = by_id.get(lead["external_record_id"] or "")
        if row is None:
            row = next((by_number[k] for k in (key9(lead["phone"]), key9(lead["whatsapp"])) if k and k in by_number), None)
        if row is None:
            counts["not in the CSV"] += 1
            continue
        counts["matched"] += 1
        if lead["lead_id"] in from_sheets:
            counts["updated later by a counsellor sheet (kept)"] += 1
            continue
        change = {}
        if row["source"] and lead["source"] != row["source"]:
            change["source"] = row["source"]
        if lead["stage"] != row["stage"]:
            change["stage"] = row["stage"]
        if row["reason"] and lead["status_reason"] != row["reason"]:
            change["status_reason"] = row["reason"]
        owner = counsellors.get(row["owner"]) if row["owner"] else None
        if owner and lead["assigned_counsellor_id"] != owner[0]:
            change["assigned_counsellor_id"], change["counsellor_name"] = owner
        if row["owner"] and not owner:
            counts[f"owner without account: {row['owner']}"] += 1
        if not change:
            continue
        for field in ("source", "stage", "assigned_counsellor_id"):
            if field in change:
                counts[f"change {field}"] += 1
        if "stage" in change and len(examples) < 8:
            examples.append(f"#{lead['lead_id']}: {lead['stage']} -> {change['stage']} ({row['record_id']})")
        changes.append((lead, row, change))
    for label, n in sorted(counts.items()):
        print(f"  {label}: {n:,}")
    print(f"  leads to change: {len(changes):,}")
    for line in examples:
        print("  e.g.", line)
    if not apply:
        print("Preview only; add --apply to write.")
        await engine.dispose()
        return

    async with engine.begin() as conn:
        for lead, row, change in changes:
            values = dict(change)
            if values.get("stage") == "enrolled" and lead["enrolled_at"] is None and row["changed"]:
                values["enrolled_at"] = row["changed"]
            if "assigned_counsellor_id" in values:
                values["assigned_at"] = datetime.now()
            sets = ", ".join(f"{k} = :{k}" for k in values)
            await conn.execute(text(f"UPDATE crm_leads SET {sets}, updated_at = now() WHERE lead_id = :lead_id"),
                               {**values, "lead_id": lead["lead_id"]})
            parts = []
            if "stage" in change:
                parts.append(f"stage {LABELS.get(lead['stage'], lead['stage'])} -> {LABELS[change['stage']]}")
            if "source" in change:
                parts.append(f"source {lead['source']} -> {change['source']}")
            if "counsellor_name" in change:
                parts.append(f"counsellor -> {change['counsellor_name']}")
            await conn.execute(text("INSERT INTO crm_activities (lead_id, activity_type, content) VALUES (:id, 'note', :c)"),
                               {"id": lead["lead_id"], "c": f"Matched to the CRM export ({row['record_id']}): " + "; ".join(parts) + "."})
    print(f"Updated {len(changes):,} leads.")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.csv, args.apply))
