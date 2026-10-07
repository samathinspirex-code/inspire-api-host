"""Preview or apply corrections from CRM-corrected.xlsx to prior CRM imports.

Read-only by default. Only leads linked to the earlier chamath / recorrected
imports are eligible. Existing nonblank fields, counsellor assignments, stages,
programmes, and any record changed in CRM after the workbook's last modification
are preserved. The fourteen previously excluded duplicate rows remain excluded.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from pydantic import EmailStr, TypeAdapter
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import engine


WORKBOOK = Path(r"C:\Users\Samath\Documents\inspire - Copy\CRM-corrected.xlsx")
SHA256 = "a732c1793213131135be258ca4b35ce7ba45eb860dd5ac9c63d0744ada40a001"
REVIEWED_PREFIX = "Legacy import: Recorrected pipeline.xlsx. Original workbook rows: "
VALID_EMAIL = TypeAdapter(EmailStr)
FILL_FIELDS = {
    "Amount": "amount",
    "School": "school",
    "Nationality": "nationality",
    "Faculty  (Schools)": "faculty",
    "Student Highest Education Qualification": "highest_qualification",
    "City": "city",
    "Country": "country",
    "Social Lead ID": "social_lead_id",
    "Description": "message",
}
UPDATE_FIELDS = (
    "created_at", "updated_at", "assigned_at", "enrolled_at", "email",
    "phone", "whatsapp", "amount", "school", "nationality", "faculty",
    "highest_qualification", "city", "country", "social_lead_id", "message",
    "status_remarks", "enrollment_email_status",
)


def clean(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def phone(value: Any) -> str | None:
    digits = re.sub(r"\D", "", clean(value) or "")
    if len(digits) == 10 and digits.startswith("0"):
        return "94" + digits[1:]
    if len(digits) == 9 and digits.startswith("7"):
        return "94" + digits
    return digits if len(digits) >= 7 else None


def display_phone(value: Any) -> str | None:
    raw = clean(value)
    digits = phone(raw)
    if not digits:
        return None
    if digits.startswith("94") and len(digits) == 11:
        return "+" + digits
    if raw and raw.startswith("+"):
        return raw
    return raw


def source_rows() -> dict[int, dict]:
    with WORKBOOK.open("rb") as handle:
        if hashlib.sha256(handle.read()).hexdigest() != SHA256:
            raise ValueError("Workbook changed since review; rerun the preview")
    sheet = load_workbook(WORKBOOK, read_only=True, data_only=True).active
    iterator = sheet.iter_rows(values_only=True)
    headers = next(iterator)
    if headers[:8] != (
        "Students Pipeline Owner", "Created Time", "Amount",
        "Students Pipeline Name", "Closing Date", "Status", "Lead Source",
        "Created By",
    ):
        raise ValueError("Unexpected workbook columns")
    rows = {row_no: dict(zip(headers, row))
            for row_no, row in enumerate(iterator, start=2)
            if any(value is not None for value in row)}
    if len(rows) != 8388:
        raise ValueError(f"Expected 8,388 workbook rows, found {len(rows):,}")
    return rows


async def load_leads() -> list[dict]:
    leads: list[dict] = []
    last_id = 0
    while True:
        async with engine.connect() as db:
            page = (await db.execute(text("""
                SELECT lead_id, external_record_id, notes, full_name, email,
                       phone, whatsapp, created_at, updated_at, assigned_at,
                       enrolled_at, counsellor_name, assigned_counsellor_id,
                       stage, amount, school, nationality, faculty,
                       highest_qualification, city, country, social_lead_id,
                       message, status_remarks, enrollment_email_status
                FROM crm_leads WHERE lead_id > :last_id
                ORDER BY lead_id LIMIT 500
            """), {"last_id": last_id})).mappings().all()
        if not page:
            break
        leads.extend(dict(row) for row in page)
        last_id = page[-1]["lead_id"]
    return leads


def match_rows(source: dict[int, dict], leads: list[dict]):
    direct: dict[int, int] = {}
    by_phone: dict[str, set[int]] = defaultdict(set)
    by_email: dict[str, set[int]] = defaultdict(set)
    by_id = {lead["lead_id"]: lead for lead in leads}
    for lead in leads:
        for value in (lead["phone"], lead["whatsapp"]):
            if (number := phone(value)):
                by_phone[number].add(lead["lead_id"])
        if lead["email"]:
            by_email[lead["email"].casefold().strip()].add(lead["lead_id"])
        external = lead["external_record_id"] or ""
        chamath = re.fullmatch(r"chamath-(?:row-(\d+)|merge-(\d+)-(\d+))", external)
        if chamath:
            for row_no in chamath.groups():
                if row_no:
                    direct[int(row_no)] = lead["lead_id"]
        if external.startswith("recorrected-") and (lead["notes"] or "").startswith(REVIEWED_PREFIX):
            audit, _ = json.JSONDecoder().raw_decode(lead["notes"][len(REVIEWED_PREFIX):])
            for item in audit:
                direct[int(item["row"]) + 499] = lead["lead_id"]
    matched = dict(direct)
    ambiguous: list[int] = []
    unmatched: list[int] = []
    for row_no, item in source.items():
        if row_no in matched:
            continue
        candidates = set().union(*(
            by_phone[phone(item[field])]
            for field in ("Phone", "Mobile", "Contact Name") if phone(item[field])
        ))
        if item["Email"]:
            candidates.update(by_email[clean(item["Email"]).casefold()])
        # The inferred target must itself belong to a prior import. This
        # excludes unrelated CRM records that happen to share a number.
        eligible = {lead_id for lead_id in candidates if (
            (by_id[lead_id]["external_record_id"] or "").startswith(("chamath-", "recorrected-"))
        )}
        if len(eligible) == 1:
            matched[row_no] = eligible.pop()
        elif eligible:
            ambiguous.append(row_no)
        else:
            unmatched.append(row_no)
    grouped: dict[int, list[int]] = defaultdict(list)
    for row_no, lead_id in matched.items():
        grouped[lead_id].append(row_no)
    return grouped, direct, ambiguous, unmatched


def valid_emails(source: dict[int, dict], grouped: dict[int, list[int]]):
    by_lead = defaultdict(set)
    invalid_rows: list[int] = []
    workbook_occurrences = Counter()
    for lead_id, row_numbers in grouped.items():
        for row_no in row_numbers:
            raw = clean(source[row_no]["Email"])
            if not raw:
                continue
            if row_no == 49 and raw == "samindiweerasiri.@gmail.com":
                raw = "samindi@inspire.college"
            try:
                address = str(VALID_EMAIL.validate_python(raw)).casefold()
                by_lead[lead_id].add(address)
                workbook_occurrences[address] += 1
            except ValueError:
                invalid_rows.append(row_no)
    repeated = {address for address, count in workbook_occurrences.items() if count > 1}
    return by_lead, invalid_rows, repeated


def latest_nonblank(items: list[dict], field: str):
    for item in sorted(items, key=lambda row: row["Modified Time"], reverse=True):
        value = item[field]
        if value is not None and clean(value):
            return value
    return None


def propose(source: dict[int, dict], leads: list[dict], grouped: dict[int, list[int]],
            allow_shared_emails: bool,
            suppress_enrollment_emails: bool, hold_enrolled_emails: bool):
    by_id = {lead["lead_id"]: lead for lead in leads}
    email_by_lead, invalid_rows, repeated = valid_emails(source, grouped)
    existing_email_owners = defaultdict(set)
    for lead in leads:
        if lead["email"]:
            existing_email_owners[lead["email"].casefold().strip()].add(lead["lead_id"])
    updates = []
    changes = Counter()
    held_emails = []
    held_enrolled_emails = []
    skipped_newer = []
    for lead_id, row_numbers in grouped.items():
        lead = by_id[lead_id]
        items = [source[row_no] for row_no in row_numbers]
        last_modified = max(item["Modified Time"] for item in items)
        if lead["updated_at"] and lead["updated_at"] > last_modified:
            skipped_newer.append(lead_id)
            continue
        values = {field: lead[field] for field in UPDATE_FIELDS}
        original = dict(values)
        created = min((item["Created Time"] for item in items
                       if isinstance(item["Created Time"], datetime)), default=None)
        if created and created != lead["created_at"]:
            values["created_at"] = created
            if lead["assigned_at"] == lead["created_at"]:
                values["assigned_at"] = created
            if lead["stage"] == "enrolled" and lead["enrolled_at"] == lead["created_at"]:
                values["enrolled_at"] = created
        # The corrected workbook has both creation and modification dates.
        values["updated_at"] = last_modified
        for workbook_field, crm_field in FILL_FIELDS.items():
            if values[crm_field] is not None and values[crm_field] != "":
                continue
            candidate = latest_nonblank(items, workbook_field)
            if candidate is not None:
                values[crm_field] = float(candidate) if crm_field == "amount" else clean(candidate)
        if not values["status_remarks"]:
            reason = latest_nonblank(items, "Reason For Loss")
            if reason:
                values["status_remarks"] = "Legacy reason for loss: " + clean(reason)
        if not values["phone"]:
            values["phone"] = display_phone(latest_nonblank(items, "Phone") or
                                                  latest_nonblank(items, "Mobile"))
        if not values["whatsapp"]:
            values["whatsapp"] = display_phone(latest_nonblank(items, "Mobile"))
        if not values["email"]:
            emails = email_by_lead.get(lead_id, set())
            if len(emails) == 1:
                candidate = next(iter(emails))
                if lead["stage"] == "enrolled" and hold_enrolled_emails:
                    held_enrolled_emails.append(lead_id)
                elif ((candidate in repeated and not allow_shared_emails)
                        or (existing_email_owners[candidate] - {lead_id})):
                    held_emails.append(lead_id)
                else:
                    values["email"] = candidate
                    if lead["stage"] == "enrolled" and suppress_enrollment_emails:
                        values["enrollment_email_status"] = "pending_manual_delivery"
            elif len(emails) > 1:
                held_emails.append(lead_id)
        changed = [field for field in UPDATE_FIELDS if values[field] != original[field]]
        if changed:
            changes.update(changed)
            updates.append({"lead_id": lead_id, "expected_updated_at": lead["updated_at"], **values})
    return updates, changes, invalid_rows, repeated, held_emails, held_enrolled_emails, skipped_newer


async def apply_updates(updates: list[dict]):
    fields = ", ".join(f"{name}=:{name}" for name in UPDATE_FIELDS)
    query = text(f"""
        UPDATE crm_leads SET {fields}
        WHERE lead_id=:lead_id
          AND updated_at IS NOT DISTINCT FROM :expected_updated_at
    """)
    lock_query = text("""
        SELECT lead_id, updated_at FROM crm_leads
        WHERE lead_id = ANY(CAST(:ids AS integer[])) FOR UPDATE
    """)
    updates.sort(key=lambda row: row["lead_id"])
    async with engine.begin() as db:
        for start in range(0, len(updates), 200):
            batch = updates[start:start + 200]
            locked = (await db.execute(lock_query, {
                "ids": [row["lead_id"] for row in batch],
            })).mappings().all()
            actual = {row["lead_id"]: row["updated_at"] for row in locked}
            if len(locked) != len(batch) or any(
                actual.get(row["lead_id"]) != row["expected_updated_at"]
                for row in batch
            ):
                raise RuntimeError("CRM record changed during reconciliation; transaction rolled back")
            result = await db.execute(query, batch)
            if result.rowcount not in (-1, len(batch)):
                raise RuntimeError(
                    f"Affected-row check failed in batch {start}: driver reported "
                    f"{result.rowcount} for {len(batch)} rows; transaction rolled back"
                )


async def main(args):
    engine.echo = False
    source = source_rows()
    leads = await load_leads()
    grouped, direct, ambiguous, unmatched = match_rows(source, leads)
    if ambiguous or len(unmatched) != 14 or len(grouped) != 7822:
        raise ValueError("Workbook-to-CRM match counts changed; no updates applied")
    updates, changes, invalid, repeated, held, held_enrolled, skipped = propose(
        source, leads, grouped, args.allow_shared_emails,
        args.suppress_enrollment_emails, args.hold_enrolled_emails,
    )
    print(json.dumps({
        "workbook_rows": len(source), "directly_matched_rows": len(direct),
        "matched_existing_leads": len(grouped),
        "held_duplicate_rows": unmatched,
        "invalid_email_rows": invalid,
        "repeated_email_addresses": len(repeated),
        "leads_with_email_held": len(held),
        "enrolled_leads_with_email_held_for_delivery_decision": len(held_enrolled),
        "leads_with_newer_crm_edits_skipped": len(skipped),
        "leads_to_update": len(updates), "field_change_counts": dict(changes),
        "send_enrollment_emails": not args.suppress_enrollment_emails,
    }, indent=2, default=str))
    if args.apply:
        if not args.suppress_enrollment_emails:
            raise ValueError("Bulk email delivery is not implemented in this reconciliation")
        await apply_updates(updates)
        print(f"Applied {len(updates):,} existing-lead updates. No leads created.")
    else:
        print("Preview only. No CRM records changed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-shared-emails", action="store_true")
    parser.add_argument("--hold-enrolled-emails", action="store_true")
    parser.add_argument("--suppress-enrollment-emails", action="store_true", default=True)
    asyncio.run(main(parser.parse_args()))
