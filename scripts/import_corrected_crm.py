"""Review and import previously unseen contacts from Corrected CRM.xlsx.

Preview is read-only. Apply is one transaction, with workbook hash, dates,
course mappings, existing-phone checks and owner checks repeated immediately
before insertion. Existing CRM leads are never changed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import engine
from scripts.import_enrolled_workbook import clean, key, phone
from scripts.import_recorrected_pipeline import source_key, stage_fields


SOURCE_SHA256 = "db0898d2ee0ad715f0ca323e521f83f443fec7a306350c0d3ad95cd9a84833e3"
HEADERS = (
    "Students Pipeline Owner", "Created Time", "Amount", "Students Pipeline Name",
    "Closing Date", "Status", "Lead Source", "Created By", "Modified By",
    "Modified Time", "Description", "Contact Name", "Expected Revenue",
    "Reason For Loss", "Social Lead ID", "Phone", "Mobile", "Email",
    "School", "Nationality", "Faculty (Schools)", "Selected Program",
    "Student Highest Education Qualification", "City", "Country",
)
COURSE_ALIASES = {
    "finance literacy": 12,
    "hnd in ai": 3, "hnd ai": 3, "hnd i ai": 3, "hnd in ai tamil": 3,
    "hnd in bm": 6, "hn in bm": 6, "hnd in businessmanagement": 6,
    "hnd in cyber": 5, "hnd in software": 2, "hnd in netdevops": 145,
    "hnd in health": 49,
    "b com": 130,
    "mba in marketing business analytics": 114,
    "mba in business intelligence analytics": 148,
    "mba in data science analytics": 140,
    "mba in finance business analytics": 111,
    "mba in artificial intelligence": 91,
    "pg dip in ai": 35,
    "pg dip in managing people": 26,
    "pg dip in marketing": 32, "pg dip marketing": 32,
    "foundation bm": 71, "foundation it": 92,
    "ai mastery cpd": 7,
    "digital marketing": 9, "digital mastery": 9,
    "bca in ai": 124,
}
GENERIC_PROGRAMMES = {
    "", "no program", "hnd", "pg dip", "pgd computing", "ai programs",
    "business", "hnd in computing", "athe ceo s video", "-",
}
OWNER_ALIASES = {"jennifer mishel fernando": "jennifer"}
engine.echo = False


def load_decisions(path: Path) -> dict[str, Any]:
    decisions = json.loads(path.read_text(encoding="utf-8"))
    if decisions.get("source_sha256") != SOURCE_SHA256:
        raise ValueError("Decisions refer to a different source workbook")
    required = {
        "existing_phone_matches": "skip_unchanged",
        "same_phone_same_counsellor": "merge_one_lead",
        "same_phone_different_counsellors": "hold",
        "generic_programmes": "unselected_with_original_in_notes",
    }
    for field, value in required.items():
        if decisions.get(field) != value:
            raise ValueError(f"Review decision changed: {field}")
    if decisions.get("date_range_8_15_16") not in {"pending", "august_15", "august_16", "hold_group"}:
        raise ValueError("Decision for 8/15-16 is missing")
    return decisions


def parse_label(value: Any, decisions: dict[str, Any]) -> datetime:
    if isinstance(value, datetime):
        if value.year != 2026:
            raise ValueError(f"Unexpected date year: {value!r}")
        return value.replace(tzinfo=None)
    label = key(value)
    if label == "8 15 16":
        day = 16 if decisions["date_range_8_15_16"] == "august_16" else 15
        return datetime(2026, 8, day)
    month = re.fullmatch(r"july (\d{1,2})(?:st|nd|rd|th)?", clean(value).lower())
    if month:
        return datetime(2026, 7, int(month.group(1)))
    slash = re.fullmatch(r"(\d{1,2})/(\d{1,2})", clean(value))
    if slash:
        return datetime(2026, int(slash.group(2)), int(slash.group(1)))
    raise ValueError(f"Unrecognized source date: {value!r}")


def read_source(source_bytes: bytes, decisions: dict[str, Any]) -> list[dict[str, Any]]:
    sheet = load_workbook(io.BytesIO(source_bytes), read_only=True, data_only=True).active
    iterator = iter(sheet.values)
    header = tuple(clean(value) for value in next(iterator))
    if header != HEADERS:
        raise ValueError(f"Unexpected workbook headers: {header!r}")
    rows: list[dict[str, Any]] = []
    last_label: datetime | None = None
    last_label_row: int | None = None
    for number, cells in enumerate(iterator, 2):
        if not any(value is not None for value in cells):
            continue
        row = {column: cells[index] if index < len(cells) else None for index, column in enumerate(HEADERS)}
        row["_row"] = number
        if row["Created Time"] is not None and clean(row["Created Time"]):
            last_label = parse_label(row["Created Time"], decisions)
            last_label_row = number
            row["_created"] = last_label
            row["_date_inferred"] = False
        else:
            if last_label is None:
                raise ValueError(f"Blank date before first dated row: {number}")
            row["_created"] = datetime.combine(last_label.date(), datetime.min.time())
            row["_date_inferred"] = True
        row["_date_label_row"] = last_label_row
        row["_ambiguous_date"] = last_label_row == 1432
        raw_modified = row["Modified Time"]
        row["_modified"] = raw_modified if isinstance(raw_modified, datetime) and raw_modified.year == 2026 else row["_created"]
        row["_phone_parts"] = {}
        row["_phone_keys"] = set()
        for field in ("Phone", "Mobile"):
            normalized, display = phone(row[field])
            row["_phone_parts"][field] = (normalized, display)
            if normalized:
                row["_phone_keys"].add(normalized)
        if clean(row["Email"]):
            raise ValueError(f"Unexpected email in row {number}; review before import")
        rows.append(row)
    if len(rows) != 1887 or rows[-1]["_row"] != 1888:
        raise ValueError(f"Expected 1,887 rows through 1888; got {len(rows)}")
    if sum(row["_date_inferred"] for row in rows) != 473:
        raise ValueError("The missing-date count changed")
    return rows


def group_rows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    seen: dict[str, int] = {}
    for index, row in enumerate(rows):
        for number in row["_phone_keys"]:
            if number in seen:
                parent[find(index)] = find(seen[number])
            else:
                seen[number] = index
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[find(index)].append(row)
    return list(groups.values())


def course_for(raw: Any, catalogue: dict[int, dict[str, Any]], by_title: dict[str, int]) -> dict[str, Any] | None:
    label = key(raw)
    if label in GENERIC_PROGRAMMES:
        return None
    course_id = COURSE_ALIASES.get(label) or by_title.get(label)
    if course_id is None or course_id not in catalogue:
        raise ValueError(f"Unmapped active course: {raw!r}")
    return catalogue[course_id]


def prepare(group: list[dict[str, Any]], catalogue: dict[int, dict[str, Any]], by_title: dict[str, int], digest: str) -> dict[str, Any]:
    ordered = sorted(group, key=lambda row: (row["_created"], row["_row"]))
    first, latest = ordered[0], ordered[-1]
    status_row = next((row for row in reversed(ordered) if key(row["Status"]) == "payment done"), latest)
    status = clean(status_row["Status"])
    if key(status) == "texting through whatsapp discussing the details":
        stage = {"stage": "contacted", "status_reason": None, "status_remarks": status,
                 "affordability_reason": None, "delay_reason": None, "enrollment_email_status": None}
    else:
        stage = stage_fields(status)
    programme_row = next((row for row in reversed(ordered) if key(row["Selected Program"]) not in GENERIC_PROGRAMMES), None)
    raw_programme = clean(programme_row["Selected Program"]) if programme_row else ""
    course = course_for(raw_programme, catalogue, by_title) if programme_row else None
    name_row = next((row for row in reversed(ordered) if clean(row["Contact Name"])), None)
    phone_row = next((row for row in reversed(ordered) if row["_phone_parts"]["Phone"][0] or row["_phone_parts"]["Mobile"][0]), None)
    primary = next((phone_row["_phone_parts"][field][1] for field in ("Phone", "Mobile") if phone_row and phone_row["_phone_parts"][field][0]), None)
    secondary = None
    if phone_row:
        mobile_key, mobile_display = phone_row["_phone_parts"]["Mobile"]
        if mobile_key and mobile_key != phone_row["_phone_parts"]["Phone"][0]:
            secondary = mobile_display
    audit_rows = []
    for row in sorted(group, key=lambda item: item["_row"]):
        audit_rows.append({"row": row["_row"], "source": {field: clean(row[field]) for field in HEADERS},
                           "effective_created_at": row["_created"].isoformat(sep=" "),
                           "date_carried_from_row": row["_date_label_row"] if row["_date_inferred"] else None})
    notes = "Legacy import: Corrected CRM.xlsx. Workbook rows: " + json.dumps(audit_rows, ensure_ascii=False)
    if not course and any(clean(row["Selected Program"]) for row in group):
        notes += "\nOriginal programme label retained; CRM course left unselected because the label is broad or unspecified."
    if stage["stage"] == "enrolled":
        notes += "\nLegacy Enrolled exception: email pending; do not send offer or registration email until a valid address is supplied."
    if not primary:
        notes += "\nNo usable phone number in workbook; contact number pending correction."
    owner_key = OWNER_ALIASES.get(key(latest["Created By"]), key(latest["Created By"]))
    description = next((clean(row["Description"]) for row in reversed(ordered) if clean(row["Description"])), None)
    return {
        "full_name": clean(name_row["Contact Name"]) if name_row else (primary or ""),
        "email": None, "phone": primary, "whatsapp": secondary,
        "interested_course": course["title"] if course else None,
        "interested_programme": course["programme_name"] if course else None,
        "academic_course_id": course["course_id"] if course else None,
        "awarding_body": course["awarding_body"] if course else None,
        "programme_fee": course["programme_fee"] if course else None,
        "programme_duration": course["duration"] if course else None,
        "source": source_key(latest["Lead Source"]), "priority": "medium",
        "message": description, "notes": notes,
        "created_at": first["_created"], "updated_at": max(row["_modified"] for row in ordered),
        "assigned_at": first["_created"],
        "enrolled_at": status_row["_created"] if stage["stage"] == "enrolled" else None,
        "external_record_id": f"corrected-crm-{digest[:12]}-row-{min(row['_row'] for row in group)}",
        "is_archived": False,
        "_owner_key": owner_key, "_source_rows": [row["_row"] for row in group],
        "_phone_keys": set().union(*(row["_phone_keys"] for row in group)),
        "_ambiguous_date": any(row["_ambiguous_date"] for row in group),
        **stage,
    }


async def run(source: Path, review: Path, apply: bool) -> None:
    decisions = load_decisions(review)
    source_bytes = source.read_bytes()
    digest = hashlib.sha256(source_bytes).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError("Workbook changed since review")
    rows = read_source(source_bytes, decisions)
    groups = group_rows(rows)
    async with engine.begin() as db:
        catalogue_rows = (await db.execute(text("""
            SELECT c.course_id, c.title, c.awarding_body, p.name AS programme_name,
                   o.price AS programme_fee, o.duration
            FROM academic_courses c JOIN academic_programmes p ON p.programme_id=c.programme_id
            JOIN academic_schools s ON s.school_id=c.school_id
            LEFT JOIN LATERAL (
                SELECT price, duration FROM academic_course_study_options
                WHERE course_id=c.course_id AND is_enabled=true
                ORDER BY price, study_option_id LIMIT 1
            ) o ON true
            WHERE c.status='active' AND p.status='active' AND s.status='active'
        """))).mappings().all()
        catalogue = {row["course_id"]: dict(row) for row in catalogue_rows}
        by_title = {key(row["title"]): row["course_id"] for row in catalogue_rows}
        users = (await db.execute(text("""
            SELECT u.user_id, u.full_name, u.is_active,
                   coalesce(bool_or(al.access_key='COUNSELLOR'),false) AS is_counsellor
            FROM users u LEFT JOIN user_access_levels ua ON ua.user_id=u.user_id
            LEFT JOIN access_levels al ON al.access_level_id=ua.access_level_id
            GROUP BY u.user_id,u.full_name,u.is_active
        """))).mappings().all()
        owners = {key(user["full_name"]): dict(user) for user in users if user["full_name"]}
        existing = (await db.execute(text("SELECT lead_id,phone,whatsapp,external_record_id FROM crm_leads"))).mappings().all()
        db_numbers = {phone(value)[0] for lead in existing for value in (lead["phone"], lead["whatsapp"])} - {None}
        external_ids = {lead["external_record_id"] for lead in existing if lead["external_record_id"]}
        prepared = [prepare(group, catalogue, by_title, digest) for group in groups]
        existing_groups = [lead for lead in prepared if lead["_phone_keys"] & db_numbers]
        held_owner = [lead for lead in prepared if len({OWNER_ALIASES.get(key(row["Created By"]), key(row["Created By"])) for row in rows if row["_row"] in lead["_source_rows"]}) > 1 and lead not in existing_groups]
        held_anonymous = [lead for lead in prepared if not lead["full_name"] and lead not in existing_groups]
        held_ambiguous_date = [lead for lead in prepared if lead["_ambiguous_date"] and decisions["date_range_8_15_16"] == "hold_group" and lead not in existing_groups]
        held_ids = {id(lead) for lead in existing_groups + held_owner + held_anonymous + held_ambiguous_date}
        ready = [lead for lead in prepared if id(lead) not in held_ids]
        if any(lead["external_record_id"] in external_ids for lead in ready):
            raise ValueError("At least one lead from this workbook is already imported")
        missing_owner = {lead["_owner_key"] for lead in ready if lead["_owner_key"] not in owners or not owners[lead["_owner_key"]]["is_counsellor"]}
        if missing_owner:
            raise ValueError(f"Unmatched counsellor accounts: {sorted(missing_owner)}")
        print(json.dumps({
            "workbook_rows": len(rows), "source_sha256": digest,
            "blank_dates_carried_down": sum(row["_date_inferred"] for row in rows),
            "source_phone_groups": len(groups), "collapsed_repeat_rows": len(rows) - len(groups),
            "existing_crm_groups_skipped": len(existing_groups),
            "existing_crm_rows_skipped": sum(len(lead["_source_rows"]) for lead in existing_groups),
            "held_cross_counsellor_groups": [lead["_source_rows"] for lead in held_owner],
            "held_without_name_or_phone": [lead["_source_rows"] for lead in held_anonymous],
            "held_date_range_groups": [lead["_source_rows"] for lead in held_ambiguous_date],
            "ready_leads": len(ready), "ready_source_rows": sum(len(lead["_source_rows"]) for lead in ready),
            "ready_stages": dict(Counter(lead["stage"] for lead in ready)),
            "ready_counsellors": dict(Counter(lead["_owner_key"] for lead in ready)),
            "ready_without_course": sum(lead["academic_course_id"] is None for lead in ready),
            "ready_with_carried_date": sum(any(row["_date_inferred"] for row in rows if row["_row"] in lead["_source_rows"]) for lead in ready),
            "decisions": decisions,
        }, indent=2, default=str))
        if not apply:
            print("Preview only; no database changes.")
            return
        if decisions["date_range_8_15_16"] == "pending":
            raise ValueError("The date range decision is still pending")
        expected = (
            len(existing_groups), sum(len(lead["_source_rows"]) for lead in existing_groups),
            len(held_owner), len(held_anonymous), len(ready),
            sum(len(lead["_source_rows"]) for lead in ready),
        )
        if expected != (496, 524, 9, 3, 1270, 1340):
            raise ValueError(f"The CRM or workbook changed since review: {expected}")
        if not ready:
            print("No leads ready to import")
            return
        columns = [
            "full_name", "email", "phone", "whatsapp", "interested_course",
            "interested_programme", "academic_course_id", "awarding_body", "programme_fee",
            "programme_duration", "source", "stage", "status_reason", "status_remarks",
            "affordability_reason", "delay_reason", "enrollment_email_status", "priority",
            "message", "notes", "created_at", "updated_at", "assigned_at", "enrolled_at",
            "external_record_id", "is_archived", "assigned_counsellor_id", "counsellor_name",
        ]
        statement = text(f"INSERT INTO crm_leads ({', '.join(columns)}) VALUES ({', '.join(':'+field for field in columns)})")
        payload = []
        for lead in ready:
            owner = owners[lead["_owner_key"]]
            values = {field: lead.get(field) for field in columns}
            values["assigned_counsellor_id"] = owner["user_id"]
            values["counsellor_name"] = owner["full_name"]
            payload.append(values)
        for offset in range(0, len(payload), 200):
            await db.execute(statement, payload[offset:offset + 200])
        print(f"Imported {len(payload)} new leads; existing CRM leads unchanged. No emails were sent.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.source, args.review, args.apply))


if __name__ == "__main__":
    main()
