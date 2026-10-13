"""Preview, then import the reviewed Enrolled.xlsx leads directly into CRM.

The decisions file records the user's choices for dates, courses, payment
amounts, the missing counsellor account, and the reviewed duplicate. No
database writes occur without --apply. A changed workbook or any unexpected
phone duplicate aborts the import.
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
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import engine


HEADERS = (
    "Students Pipeline Owner", "Created Time", "Amount", "Students Pipeline Name",
    "Closing Date", "Status", "Lead Source", "Created By", "Modified By",
    "Modified Time", "Description", "Contact Name", "Expected Revenue",
    "Reason For Loss", "Social Lead ID", "Phone", "Mobile", "Email",
    "School", "Nationality", "Faculty  (Schools)", "Selected Program",
    "Student Highest Education Qualification", "City", "Country",
)
EXPECTED_ROWS = 33
OWNER_ALIASES = {"jennifer mishel fernando": "jennifer"}
KNOWN_PROGRAMMES = {
    "finance literacy": "Finance Literacy",
    "hnd in computing software engineering software developer pathway": "HND in Computing (Software Engineering)",
}
REVIEW_PROGRAMMES = {"hnd in ai", "digital marketing", "digital marketing mastery"}


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean(value).lower()).strip()


def phone(value: Any) -> tuple[str | None, str | None]:
    raw = clean(value)
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10 and digits.startswith("0"):
        return f"94{digits[1:]}", f"+94{digits[1:]}"
    if len(digits) == 11 and digits.startswith("94"):
        return digits, f"+{digits}"
    if len(digits) == 9 and digits.startswith("7"):
        return f"94{digits}", f"+94{digits}"
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return digits, f"+{digits}"
    if 8 <= len(digits) <= 15:
        return digits, raw
    return None, None


def read_source(path: Path) -> list[dict[str, Any]]:
    sheet = load_workbook(path, read_only=True, data_only=True).active
    values = sheet.values
    headers = tuple(clean(value) for value in next(values))
    if headers != HEADERS:
        raise ValueError(f"Unexpected source columns: {headers!r}")
    rows = [dict(zip(headers, values), _row=number) for number, values in enumerate(values, 2) if any(v is not None for v in values)]
    if len(rows) != EXPECTED_ROWS:
        raise ValueError(f"Expected {EXPECTED_ROWS} rows, got {len(rows)}")
    return rows


def decision_date(value: Any, correct_2006: bool) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"Invalid workbook date: {value!r}")
    if value.year == 2006 and correct_2006:
        return value.replace(year=2026)
    return value.replace(tzinfo=None)


def decisions_for(path: Path, digest: str) -> dict[str, Any]:
    decisions = json.loads(path.read_text(encoding="utf-8"))
    if decisions.get("source_sha256") != digest:
        raise ValueError("The workbook changed since the decisions were recorded")
    if type(decisions.get("correct_2006_to_2026")) is not bool:
        raise ValueError("Choose how to handle the three 2006 dates")
    if type(decisions.get("create_yazeed_account")) is not bool:
        raise ValueError("Choose whether to create Yazeed's account")
    if decisions.get("amount_handling") not in {"down_payment", "notes"}:
        raise ValueError("Choose how to handle Amount")
    if decisions.get("duplicate_row_18") not in {"new", "update", "hold"}:
        raise ValueError("Choose whether to create, update, or hold row 18")
    chosen = {key(name): target for name, target in decisions.get("programme_targets", {}).items()}
    if set(chosen) != REVIEW_PROGRAMMES:
        raise ValueError(f"Programme decisions required for {sorted(REVIEW_PROGRAMMES)}")
    if not all(isinstance(target, str) and target.strip() for target in chosen.values()):
        raise ValueError("Programme targets must be named")
    decisions["programme_targets"] = chosen
    return decisions


async def run(source: Path, review: Path, apply: bool) -> None:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    decisions = decisions_for(review, digest)
    rows = read_source(source)
    within_file: dict[str, int] = {}
    for row in rows:
        if key(row["Status"]) != "payment done" or key(row["Lead Source"]) != "meta whatsapp":
            raise ValueError(f"Unexpected status or source on row {row['_row']}")
        if clean(row["Email"]):
            raise ValueError(f"Row {row['_row']} has an email; review its delivery status")
        row["_phone_key"], row["_phone"] = phone(row["Phone"])
        row["_mobile_key"], row["_mobile"] = phone(row["Mobile"])
        if not row["_phone_key"] and not row["_mobile_key"]:
            raise ValueError(f"Row {row['_row']} has no usable phone or mobile")
        for number in {row["_phone_key"], row["_mobile_key"]} - {None}:
            if number in within_file:
                raise ValueError(f"Rows {within_file[number]} and {row['_row']} share a phone")
            within_file[number] = row["_row"]
        row["_created"] = decision_date(row["Created Time"], decisions["correct_2006_to_2026"])
        row["_closing"] = decision_date(row["Closing Date"], decisions["correct_2006_to_2026"])

    async with engine.begin() as db:
        catalogue = (await db.execute(text("""
            SELECT c.course_id, c.title, c.awarding_body, p.name AS programme_name,
                   o.price AS programme_fee, o.duration
            FROM academic_courses c
            JOIN academic_programmes p ON p.programme_id=c.programme_id
            JOIN academic_schools s ON s.school_id=c.school_id
            LEFT JOIN LATERAL (
                SELECT price, duration FROM academic_course_study_options
                WHERE course_id=c.course_id AND is_enabled=true
                ORDER BY price, study_option_id LIMIT 1
            ) o ON true
            WHERE c.status='active' AND p.status='active' AND s.status='active'
        """))).mappings().all()
        courses = {key(course["title"]): dict(course) for course in catalogue}
        users = (await db.execute(text("""
            SELECT u.user_id, u.full_name, u.is_active,
                   bool_or(al.access_key='COUNSELLOR') AS is_counsellor
            FROM users u
            LEFT JOIN user_access_levels ua ON ua.user_id=u.user_id
            LEFT JOIN access_levels al ON al.access_level_id=ua.access_level_id
            GROUP BY u.user_id, u.full_name, u.is_active
        """))).mappings().all()
        owners = {key(user["full_name"]): dict(user) for user in users if user["full_name"]}
        existing = (await db.execute(text("SELECT lead_id, phone, whatsapp, email, stage, external_record_id FROM crm_leads"))).mappings().all()
        existing_phone: dict[str, int] = {}
        existing_by_id = {lead["lead_id"]: lead for lead in existing}
        existing_ids = set()
        for lead in existing:
            if lead["external_record_id"]:
                existing_ids.add(lead["external_record_id"])
            for field in ("phone", "whatsapp"):
                number, _ = phone(lead[field])
                if number:
                    existing_phone[number] = lead["lead_id"]

        prepared = []
        duplicates = []
        held_duplicates = []
        held_programme_rows = []
        missing_owners = Counter()
        for row in rows:
            external_id = f"enrolled-{digest[:12]}-row-{row['_row']}"
            matches = {existing_phone[number] for number in {row["_phone_key"], row["_mobile_key"]} - {None} if number in existing_phone}
            update_id = None
            if external_id in existing_ids or matches:
                duplicates.append((row["_row"], sorted(matches)))
                if row["_row"] != 18 or matches != {25733} or external_id in existing_ids:
                    raise ValueError(f"Unexpected existing CRM match on row {row['_row']}: {sorted(matches)}")
                if decisions["duplicate_row_18"] == "hold":
                    held_duplicates.append(row["_row"])
                    continue
                if decisions["duplicate_row_18"] == "update":
                    current = existing_by_id[25733]
                    if current["stage"] != "uncontactable" or current["email"]:
                        raise ValueError("Existing lead #25733 changed since review")
                    update_id = 25733
            raw_name = clean(row["Contact Name"])
            _, name_as_phone = phone(row["Contact Name"])
            full_name = name_as_phone if re.fullmatch(r"[+\d\s()./-]+", raw_name) and name_as_phone else raw_name
            programme_name = key(row["Selected Program"])
            target = KNOWN_PROGRAMMES.get(programme_name) or decisions["programme_targets"].get(programme_name)
            if not target:
                raise ValueError(f"No programme mapping for row {row['_row']}: {row['Selected Program']!r}")
            if target == "hold":
                held_programme_rows.append(row["_row"])
                continue
            owner_name = OWNER_ALIASES.get(key(row["Created By"]), key(row["Created By"]))
            if owner_name not in owners:
                missing_owners[clean(row["Created By"])] += 1
            legacy = target.startswith("legacy:")
            course_name = target[7:].strip() if legacy else target.strip()
            course = None if legacy else courses.get(key(course_name))
            if not legacy and course is None:
                raise ValueError(f"Current CRM course not found: {course_name}")
            amount = float(row["Amount"]) if row["Amount"] is not None else None
            audit = {field: clean(row[field]) for field in HEADERS}
            notes = f"Legacy import: Enrolled.xlsx, row {row['_row']}. Original values: {json.dumps(audit, ensure_ascii=False)}"
            notes += "\nLegacy Enrolled exception: email pending; do not send offer or registration email until a valid address is supplied."
            if decisions["amount_handling"] == "down_payment":
                notes += "\nWorkbook Amount is a down payment, not the full programme fee."
                if amount is not None and course and course["programme_fee"] is not None and amount > float(course["programme_fee"]):
                    notes += "\nRecorded down payment exceeds current catalogue fee; verify amount and fee."
            if row["_row"] == 18 and decisions["duplicate_row_18"] == "new":
                notes += "\nDuplicate phone reviewed: imported as a separate new lead by request; existing CRM lead #25733 was not changed."
            if legacy:
                notes += "\nProgramme retained as legacy text; no current course linked."
            if row["Created Time"].year == 2006 and decisions["correct_2006_to_2026"]:
                notes += "\nWorkbook year 2006 corrected to 2026 by approval."
            prepared.append({
                "full_name": full_name, "phone": row["_phone"] or row["_mobile"],
                "whatsapp": row["_mobile"] if row["_mobile_key"] != row["_phone_key"] else None,
                "email": None, "source": "whatsapp", "stage": "enrolled", "priority": "medium",
                "interested_course": course_name, "interested_programme": course["programme_name"] if course else None,
                "academic_course_id": course["course_id"] if course else None,
                "awarding_body": course["awarding_body"] if course else None,
                "programme_fee": course["programme_fee"] if course else None,
                "programme_duration": course["duration"] if course else None,
                "school": clean(row["School"]) or None, "nationality": clean(row["Nationality"]) or None,
                "faculty": clean(row["Faculty  (Schools)"]) or None,
                "highest_qualification": clean(row["Student Highest Education Qualification"]) or None,
                "city": clean(row["City"]) or None, "country": clean(row["Country"]) or None,
                "amount": amount if decisions["amount_handling"] == "down_payment" else None,
                "amount_paid": amount if decisions["amount_handling"] == "down_payment" else None,
                "payment_status": "down_payment" if decisions["amount_handling"] == "down_payment" else None,
                "payment_date": row["_closing"] if decisions["amount_handling"] == "down_payment" else None,
                "enrolled_at": row["_closing"], "created_at": row["_created"],
                "updated_at": decision_date(row["Modified Time"], decisions["correct_2006_to_2026"]),
                "assigned_at": row["_created"], "enrollment_email_status": "pending_email_legacy",
                "notes": notes, "external_record_id": external_id, "is_archived": False,
                "_owner_key": owner_name, "_row": row["_row"], "_update_lead_id": update_id,
            })

        if missing_owners != Counter({"Yazeed Aman": 5}):
            if missing_owners:
                raise ValueError(f"Unexpected unmatched owners: {dict(missing_owners)}")
        if missing_owners and not decisions["create_yazeed_account"]:
            prepared = [lead for lead in prepared if lead["_owner_key"] != "yazeed aman"]
        summary = {
            "source_rows": len(rows), "source_sha256": digest,
            "existing_crm_duplicates": duplicates,
            "unmatched_owner_rows": dict(missing_owners),
            "new_leads_ready": sum(lead["_update_lead_id"] is None for lead in prepared),
            "existing_leads_to_update": [lead["_update_lead_id"] for lead in prepared if lead["_update_lead_id"] is not None],
            "held_duplicate_rows": held_duplicates,
            "held_programme_rows": held_programme_rows,
            "ready_by_owner": dict(Counter(lead["_owner_key"] for lead in prepared)),
            "legacy_programmes": dict(Counter(lead["interested_course"] for lead in prepared if lead["academic_course_id"] is None)),
            "corrected_year_rows": [lead["_row"] for lead in prepared if "Workbook year 2006 corrected" in lead["notes"]],
        }
        print(json.dumps(summary, indent=2, default=str))
        if not apply:
            print("Preview only; no database changes.")
            return
        expected_total = (33 if decisions["create_yazeed_account"] else 28) - len(held_programme_rows) - (1 if decisions["duplicate_row_18"] == "hold" else 0)
        if duplicates != [(18, [25733])] or len(prepared) != expected_total:
            raise ValueError("Import differs from the approved preview; review duplicates or held rows")

        if missing_owners and decisions["create_yazeed_account"]:
            display = "Yazeed Aman"
            placeholder = "yazeed.aman.legacy@example.com"
            if await db.scalar(text("SELECT user_id FROM users WHERE lower(email)=:email"), {"email": placeholder}):
                raise ValueError("Yazeed's placeholder email is already used")
            access_id = await db.scalar(text("SELECT access_level_id FROM access_levels WHERE access_key='COUNSELLOR' AND is_active=true"))
            if access_id is None:
                raise ValueError("COUNSELLOR access level is unavailable")
            user_id = await db.scalar(text("INSERT INTO users (full_name,email,is_active) VALUES (:name,:email,true) RETURNING user_id"), {"name": display, "email": placeholder})
            await db.execute(text("INSERT INTO user_access_levels (user_id,access_level_id) VALUES (:uid,:aid)"), {"uid": user_id, "aid": access_id})
            await db.execute(text("INSERT INTO crm_counsellor_status (user_id,is_active,assign_order) VALUES (:uid,false,9999)"), {"uid": user_id})
            owners["yazeed aman"] = {"user_id": user_id, "full_name": display, "is_counsellor": True}

        columns = [key for key in prepared[0] if not key.startswith("_")]
        columns += ["assigned_counsellor_id", "counsellor_name"]
        statement = text(f"INSERT INTO crm_leads ({', '.join(columns)}) VALUES ({', '.join(':'+column for column in columns)}) RETURNING lead_id")
        created_ids = []
        updated_ids = []
        for lead in prepared:
            owner = owners[lead["_owner_key"]]
            if not owner["is_counsellor"]:
                raise ValueError(f"Owner has no COUNSELLOR role: {owner['full_name']}")
            values = {column: lead.get(column) for column in columns}
            values["assigned_counsellor_id"] = owner["user_id"]
            values["counsellor_name"] = owner["full_name"]
            if lead["_update_lead_id"] is None:
                created_ids.append(await db.scalar(statement, values))
            else:
                await db.execute(text("""
                    UPDATE crm_leads SET
                        stage='enrolled', status_reason=NULL, status_remarks=NULL,
                        interested_course=:course, interested_programme=:programme,
                        academic_course_id=:course_id, awarding_body=:awarding_body,
                        programme_fee=:programme_fee, programme_duration=:duration,
                        assigned_counsellor_id=:counsellor_id, counsellor_name=:counsellor_name,
                        assigned_at=:assigned_at, enrolled_at=:enrolled_at,
                        amount=:amount, amount_paid=:amount_paid,
                        payment_status=:payment_status, payment_date=:payment_date,
                        enrollment_email_status='pending_email_legacy',
                        notes=concat_ws(E'\\n', NULLIF(notes,''), :notes), updated_at=now()
                    WHERE lead_id=:lead_id AND stage='uncontactable' AND email IS NULL
                """), {
                    "course": lead["interested_course"], "programme": lead["interested_programme"],
                    "course_id": lead["academic_course_id"], "awarding_body": lead["awarding_body"],
                    "programme_fee": lead["programme_fee"], "duration": lead["programme_duration"],
                    "counsellor_id": owner["user_id"], "counsellor_name": owner["full_name"],
                    "assigned_at": lead["assigned_at"], "enrolled_at": lead["enrolled_at"],
                    "amount": lead["amount"], "amount_paid": lead["amount_paid"],
                    "payment_status": lead["payment_status"], "payment_date": lead["payment_date"],
                    "notes": lead["notes"], "lead_id": lead["_update_lead_id"],
                })
                updated_ids.append(lead["_update_lead_id"])
        print(f"Imported {len(created_ids)} leads and updated {len(updated_ids)} existing leads. No emails were sent.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.source, args.review, args.apply))


if __name__ == "__main__":
    main()
