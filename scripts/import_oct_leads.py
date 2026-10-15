"""Review and directly import October leads after workbook exceptions are resolved.

Preview never writes. Apply repeats the same checks and inserts approved new
leads in one database transaction without triggering email delivery.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import engine
from scripts.import_corrected_crm import group_rows
from scripts.import_enrolled_workbook import clean, key, phone
from scripts.import_recorrected_pipeline import source_key, stage_fields


SOURCE_SHA256 = "45a07a7aac135af5f6c1479700db0e2f22322d537bcd426c948a57308d295a84"
SOURCE_HEADERS = (
    "Students Pipeline Owner", "Created Time", "Amount", "Students Pipeline Name",
    "Closing Date", "Status", "Lead Source", "Created By", "Modified By",
    "Modified Time", "Description", "Contact Name", "Expected Revenue",
    "Reason For Loss", "Social Lead ID", "Phone", "Mobile", "Email",
    "School", "Nationality", "Faculty (Schools)", "Selected Program",
    "Student Highest Education Qualification", "City", "Country",
)
COURSE_IDS = {
    "finance literacy": 12,
    "hnd in ai": 3,
    "hnd in business management": 6,
    "digital marketing analytics essentials": 9,
    "ai mastery cpd": 7,
    "ai for hr": 14,
    "health care foundation": 80,
    "hnd in cyber security": 5,
    "hnd in data analytics": 4,
    "hnd in software engineering": 2,
}
OWNER_ALIASES = {"samindi": "samindi weerasiri"}
SHARED_PHONE_GROUPS = ((11, 13), (60, 61), (108, 117, 203), (181, 190))
engine.echo = False


def normalized_phone(value: Any) -> tuple[str | None, str | None]:
    raw = clean(value)
    if raw.lower().startswith("p:"):
        raw = raw[2:].strip()
    return phone(raw)


def read_rows(source: Path) -> tuple[str, list[dict[str, Any]]]:
    source_bytes = source.read_bytes()
    digest = hashlib.sha256(source_bytes).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError("October workbook changed; review the revised file before importing")
    sheet = load_workbook(source, read_only=True, data_only=True).active
    values = iter(sheet.values)
    header = tuple(next(values))
    if tuple(clean(value) for value in header[:25]) != SOURCE_HEADERS or any(clean(value) for value in header[25:]):
        raise ValueError("October workbook columns changed")
    rows = []
    for number, cells in enumerate(values, 2):
        if not any(value is not None for value in cells):
            continue
        if any(clean(value) for value in cells[25:]):
            raise ValueError(f"Unexpected extra data in row {number}")
        row = dict(zip(SOURCE_HEADERS, cells[:25]))
        row["_row"] = number
        created = row["Created Time"]
        if not isinstance(created, datetime) or created.year != 2026 or created.month != 10:
            raise ValueError(f"Unexpected creation date in row {number}: {created!r}")
        row["_created"] = created.replace(tzinfo=None)
        modified = row["Modified Time"]
        row["_modified"] = modified.replace(tzinfo=None) if isinstance(modified, datetime) else row["_created"]
        row["_phone_parts"] = {field: normalized_phone(row[field]) for field in ("Phone", "Mobile", "Students Pipeline Name")}
        row["_phone_keys"] = {part[0] for part in row["_phone_parts"].values() if part[0]}
        if clean(row["Email"]):
            raise ValueError(f"Unexpected email in row {number}; review before import")
        rows.append(row)
    if len(rows) != 233 or rows[-1]["_row"] != 234:
        raise ValueError(f"Expected 233 source rows ending at 234; got {len(rows)}")
    return digest, rows


def load_decisions(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("source_sha256") != SOURCE_SHA256:
        raise ValueError("Decisions refer to a different workbook")
    return result


def owner_key(row: dict[str, Any], decisions: dict[str, Any]) -> str:
    created_by = key(row["Created By"])
    if not created_by and decisions.get("blank_created_by") == "pipeline_owner":
        created_by = key(row["Students Pipeline Owner"])
    return OWNER_ALIASES.get(created_by, created_by)


def audit_notes(group: list[dict[str, Any]], extra: list[str]) -> str:
    source = [
        {"workbook_row": row["_row"], **{field: clean(row[field]) for field in SOURCE_HEADERS}}
        for row in sorted(group, key=lambda value: value["_row"])
    ]
    return "Legacy import: oct leads .xlsx. Original workbook values: " + json.dumps(source, ensure_ascii=False) + (
        "\n" + "\n".join(extra) if extra else ""
    )


def prepare(group: list[dict[str, Any]], courses: dict[int, dict[str, Any]], decisions: dict[str, Any], digest: str) -> dict[str, Any]:
    ordered = sorted(group, key=lambda row: (row["_created"], row["_row"]))
    first, latest = ordered[0], ordered[-1]
    payment = next((row for row in reversed(ordered) if key(row["Status"]) == "payment done"), None)
    status_row = payment or latest
    stage = stage_fields(clean(status_row["Status"]))
    programme_row = next((row for row in reversed(ordered) if clean(row["Selected Program"])), None)
    programme = key(programme_row["Selected Program"]) if programme_row else ""
    if programme == "free foundation hnd" and decisions.get("free_foundation_hnd") in {"unselected", "hold"}:
        course = None
    elif programme:
        course_id = COURSE_IDS.get(programme)
        if course_id is None or course_id not in courses:
            raise ValueError(f"Unmapped or inactive course: {programme_row['Selected Program']!r}")
        course = courses[course_id]
    else:
        course = None
    phone_row = next((row for row in reversed(ordered) if row["_phone_parts"]["Phone"][0] or row["_phone_parts"]["Mobile"][0] or row["_phone_parts"]["Students Pipeline Name"][0]), None)
    primary = next((phone_row["_phone_parts"][field][1] for field in ("Phone", "Mobile", "Students Pipeline Name") if phone_row and phone_row["_phone_parts"][field][0]), None)
    secondary = None
    if phone_row:
        for field in ("Mobile", "Students Pipeline Name"):
            number, display = phone_row["_phone_parts"][field]
            if number and number != normalized_phone(primary)[0]:
                secondary = display
                break
    name = next((clean(row["Contact Name"]) for row in reversed(ordered) if clean(row["Contact Name"])), None)
    if not name:
        name = primary or f"Imported lead row {first['_row']}"
    owners = {owner_key(row, decisions) for row in group}
    owner = next(iter(owners)) if len(owners) == 1 else ""
    extras = []
    if programme == "free foundation hnd":
        extras.append("Original programme is broad; CRM course left unselected pending course confirmation.")
    if not primary:
        extras.append("No usable contact number in workbook; correction pending.")
    if stage["stage"] == "enrolled":
        extras.append("Legacy Enrolled exception: email pending; do not send enrollment documents until a valid email is supplied.")
    return {
        "full_name": name, "email": None, "phone": primary, "whatsapp": secondary,
        "interested_course": course["title"] if course else None,
        "interested_programme": course["programme_name"] if course else None,
        "academic_course_id": course["course_id"] if course else None,
        "awarding_body": course["awarding_body"] if course else None,
        "programme_fee": course["programme_fee"] if course else None,
        "programme_duration": course["duration"] if course else None,
        "source": source_key(latest["Lead Source"]), "priority": "medium",
        "message": next((clean(row["Description"]) for row in reversed(ordered) if clean(row["Description"])), None),
        "notes": audit_notes(group, extras),
        "created_at": first["_created"], "updated_at": max(row["_modified"] for row in group),
        "assigned_at": first["_created"],
        "enrolled_at": status_row["_created"] if stage["stage"] == "enrolled" else None,
        "external_record_id": f"oct-leads-{digest[:12]}-row-{min(row['_row'] for row in group)}",
        "is_archived": False,
        "_owner": owner, "_owners": owners, "_rows": [row["_row"] for row in group],
        "_phones": set().union(*(row["_phone_keys"] for row in group)),
        **stage,
    }


async def run(source: Path, decisions_path: Path, apply: bool) -> None:
    digest, rows = read_rows(source)
    decisions = load_decisions(decisions_path)
    selected = decisions.get("selected_shared_phone_rows")
    if selected is not None:
        if not isinstance(selected, list) or any(type(number) is not int for number in selected):
            raise ValueError("Selected shared-phone rows must be workbook row numbers")
        groups_by_row = {row["_row"]: row for row in rows}
        expected = {number for group in SHARED_PHONE_GROUPS for number in group}
        if len(selected) != len(SHARED_PHONE_GROUPS) or any(sum(number in selected for number in group) != 1 for group in SHARED_PHONE_GROUPS):
            raise ValueError("Select exactly one row from each shared-phone group")
        if any(len({next(iter(groups_by_row[number]["_phone_keys"])) for number in group}) != 1 for group in SHARED_PHONE_GROUPS):
            raise ValueError("Shared-phone groups changed in the workbook")
        discarded = expected - set(selected)
        active_rows = [row for row in rows if row["_row"] not in discarded]
    else:
        discarded = set()
        active_rows = rows
    groups = group_rows(active_rows)
    all_keys = sorted(set().union(*(row["_phone_keys"] for row in active_rows)))
    local_suffixes = sorted({number[-9:] for number in all_keys if len(number) == 11 and number.startswith("94")})
    async with engine.begin() as db:
        course_rows = (await db.execute(text("""
            SELECT c.course_id,c.title,c.awarding_body,p.name AS programme_name,o.price AS programme_fee,o.duration
            FROM academic_courses c JOIN academic_programmes p ON p.programme_id=c.programme_id
            JOIN academic_schools s ON s.school_id=c.school_id
            LEFT JOIN LATERAL (
                SELECT price,duration FROM academic_course_study_options
                WHERE course_id=c.course_id AND is_enabled=true ORDER BY price,study_option_id LIMIT 1
            ) o ON true
            WHERE c.status='active' AND p.status='active' AND s.status='active'
        """))).mappings().all()
        courses = {row["course_id"]: dict(row) for row in course_rows}
        users = (await db.execute(text("""
            SELECT u.user_id,u.full_name,u.is_active,
                   coalesce(bool_or(al.access_key='COUNSELLOR'),false) AS is_counsellor
            FROM users u LEFT JOIN user_access_levels ua ON ua.user_id=u.user_id
            LEFT JOIN access_levels al ON al.access_level_id=ua.access_level_id
            GROUP BY u.user_id,u.full_name,u.is_active
        """))).mappings().all()
        owners = {key(user["full_name"]): dict(user) for user in users if user["full_name"] and user["is_active"] and user["is_counsellor"]}
        candidates = (await db.execute(text("""
            SELECT lead_id,phone,whatsapp,external_record_id FROM crm_leads
            WHERE regexp_replace(coalesce(phone,''),'[^0-9]','','g')=any(:keys)
               OR regexp_replace(coalesce(whatsapp,''),'[^0-9]','','g')=any(:keys)
               OR right(regexp_replace(coalesce(phone,''),'[^0-9]','','g'),9)=any(:suffixes)
               OR right(regexp_replace(coalesce(whatsapp,''),'[^0-9]','','g'),9)=any(:suffixes)
        """), {"keys": all_keys, "suffixes": local_suffixes})).mappings().all()
        prior = {}
        for candidate in candidates:
            for field in ("phone", "whatsapp"):
                number = normalized_phone(candidate[field])[0]
                if number:
                    prior.setdefault(number, set()).add(candidate["lead_id"])
        prepared = [prepare(group, courses, decisions, digest) for group in groups]
        existing = [lead for lead in prepared if any(number in prior for number in lead["_phones"])]
        held_shared = [lead for lead in prepared if len(lead["_owners"]) > 1 and lead not in existing]
        held_no_owner = [lead for lead in prepared if (not lead["_owner"] or lead["_owner"] not in owners) and lead not in existing and lead not in held_shared]
        held_no_phone = [lead for lead in prepared if not lead["phone"] and lead not in existing and lead not in held_shared and lead not in held_no_owner
                         and (decisions.get("no_phone_rows") != "import_80_215" or lead["_rows"][0] not in {80, 215})]
        held_broad_course = [lead for lead in prepared if decisions.get("free_foundation_hnd") == "hold" and any(key(row["Selected Program"]) == "free foundation hnd" for row in rows if row["_row"] in lead["_rows"])
                             and lead not in existing and lead not in held_shared and lead not in held_no_owner and lead not in held_no_phone]
        held_ids = {id(lead) for lead in existing + held_shared + held_no_owner + held_no_phone + held_broad_course}
        ready = [lead for lead in prepared if id(lead) not in held_ids]
        summary = {
            "source_rows": len(rows), "discarded_shared_phone_rows": sorted(discarded),
            "source_groups": len(groups), "existing_phone_rows": sum(len(lead["_rows"]) for lead in existing),
            "existing_phone_groups": len(existing), "held_shared_phone_groups": [lead["_rows"] for lead in held_shared],
            "held_without_owner": [lead["_rows"] for lead in held_no_owner],
            "held_without_phone": [lead["_rows"] for lead in held_no_phone],
            "held_broad_course": [lead["_rows"] for lead in held_broad_course],
            "ready_leads": len(ready), "ready_source_rows": sum(len(lead["_rows"]) for lead in ready),
            "ready_by_stage": dict(Counter(lead["stage"] for lead in ready)),
            "ready_by_owner": dict(Counter(lead["_owner"] for lead in ready)),
            "ready_without_course": sum(lead["academic_course_id"] is None for lead in ready),
            "source_sha256": digest,
        }
        print(json.dumps(summary, indent=2))
        if not apply:
            print("Preview only; no database changes.")
            return
        if decisions.get("blank_created_by") not in {"pipeline_owner", "hold"} or decisions.get("free_foundation_hnd") not in {"unselected", "hold"} or decisions.get("no_phone_rows") not in {"import_80_215", "hold"}:
            raise ValueError("Review decisions are still pending")
        if decisions.get("expected_ready_leads") != len(ready) or decisions.get("expected_existing_phone_rows") != summary["existing_phone_rows"]:
            raise ValueError("CRM matches changed since preview; re-review before applying")
        ids = [lead["external_record_id"] for lead in ready]
        already = (await db.execute(text("SELECT external_record_id FROM crm_leads WHERE external_record_id=any(:ids)"), {"ids": ids})).scalars().all()
        if already:
            raise ValueError(f"At least {len(already)} rows from this workbook were already imported")
        columns = [
            "full_name", "email", "phone", "whatsapp", "interested_course", "interested_programme",
            "academic_course_id", "awarding_body", "programme_fee", "programme_duration", "source", "stage",
            "status_reason", "status_remarks", "affordability_reason", "delay_reason", "enrollment_email_status",
            "priority", "message", "notes", "created_at", "updated_at", "assigned_at", "enrolled_at",
            "external_record_id", "is_archived", "assigned_counsellor_id", "counsellor_name",
        ]
        payload = []
        for lead in ready:
            values = {field: lead.get(field) for field in columns}
            owner = owners[lead["_owner"]]
            values["assigned_counsellor_id"] = owner["user_id"]
            values["counsellor_name"] = owner["full_name"]
            payload.append(values)
        statement = text(f"INSERT INTO crm_leads ({', '.join(columns)}) VALUES ({', '.join(':'+field for field in columns)})")
        for offset in range(0, len(payload), 100):
            await db.execute(statement, payload[offset:offset + 100])
        print(f"Imported {len(payload)} new October leads. Existing CRM leads unchanged. No emails sent.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.source, args.decisions, args.apply))


if __name__ == "__main__":
    main()
