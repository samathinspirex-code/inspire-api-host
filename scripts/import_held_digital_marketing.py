"""Import the three previously held Enrolled.xlsx Digital Marketing rows.

Requires the reviewed decision mapping both legacy names to the Essentials
course. The original 30 imported rows are checked, never written again.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import engine
from scripts.import_enrolled_workbook import clean, decision_date, decisions_for, key, phone, read_source, HEADERS


HELD_ROWS = (5, 6, 28)
TARGET = "Digital Marketing & Analytics Essentials"
APPROVED_PHONE_MATCHES = {5: set(), 6: {26539}, 28: set()}


async def run(source: Path, review: Path, apply: bool) -> None:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    decisions = decisions_for(review, digest)
    if decisions["programme_targets"].get("digital marketing") != TARGET or decisions["programme_targets"].get("digital marketing mastery") != TARGET:
        raise ValueError("Both held programme names must be approved for the Essentials course")
    rows = read_source(source)
    selected = [row for row in rows if row["_row"] in HELD_ROWS]
    if tuple(row["_row"] for row in selected) != HELD_ROWS:
        raise ValueError("Held rows changed")
    for row in selected:
        if key(row["Status"]) != "payment done" or key(row["Lead Source"]) != "meta whatsapp" or clean(row["Email"]):
            raise ValueError(f"Unexpected status, source, or email on row {row['_row']}")

    async with engine.begin() as db:
        course = (await db.execute(text("""
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
            WHERE c.title=:title AND c.status='active' AND p.status='active' AND s.status='active'
        """), {"title": TARGET})).mappings().one()
        users = (await db.execute(text("""
            SELECT u.user_id, u.full_name, u.is_active,
                   coalesce(bool_or(al.access_key='COUNSELLOR'), false) AS is_counsellor
            FROM users u
            LEFT JOIN user_access_levels ua ON ua.user_id=u.user_id
            LEFT JOIN access_levels al ON al.access_level_id=ua.access_level_id
            GROUP BY u.user_id, u.full_name, u.is_active
        """))).mappings().all()
        owners = {key(user["full_name"]): user for user in users}
        existing = (await db.execute(text("SELECT lead_id, phone, whatsapp, external_record_id FROM crm_leads"))).mappings().all()
        existing_ids = {lead["external_record_id"] for lead in existing if lead["external_record_id"]}
        original_prefix = f"enrolled-{digest[:12]}-row-"
        previously_imported = {int(value.removeprefix(original_prefix)) for value in existing_ids if value.startswith(original_prefix)}
        if previously_imported != set(range(2, 35)) - set(HELD_ROWS):
            raise ValueError(f"The original import no longer contains exactly its 30 approved rows: {sorted(previously_imported)}")
        prepared = []
        for row in selected:
            number = row["_row"]
            external_id = f"{original_prefix}{number}"
            if external_id in existing_ids:
                raise ValueError(f"Row {number} already imported")
            primary_key, primary = phone(row["Phone"])
            mobile_key, mobile = phone(row["Mobile"])
            if not primary_key and not mobile_key:
                raise ValueError(f"Row {number} has no usable number")
            matches = set()
            for lead in existing:
                for field in ("phone", "whatsapp"):
                    normalized, _ = phone(lead[field])
                    if normalized and normalized in {primary_key, mobile_key}:
                        matches.add(lead["lead_id"])
            if matches != APPROVED_PHONE_MATCHES[number]:
                raise ValueError(f"Unexpected CRM phone match for row {number}: {sorted(matches)}")
            owner_key = key(row["Created By"])
            if owner_key == "jennifer mishel fernando":
                owner_key = "jennifer"
            owner = owners.get(owner_key)
            if not owner or not owner["is_active"] or not owner["is_counsellor"]:
                raise ValueError(f"No active counsellor account for row {number}: {row['Created By']}")
            amount = float(row["Amount"])
            audit = {field: clean(row[field]) for field in HEADERS}
            notes = f"Legacy import: Enrolled.xlsx, row {number}. Original values: {json.dumps(audit, ensure_ascii=False)}"
            notes += "\nLegacy Enrolled exception: email pending; do not send offer or registration email until a valid address is supplied."
            notes += "\nWorkbook Amount is a down payment, not the full programme fee."
            if course["programme_fee"] is not None and amount > float(course["programme_fee"]):
                notes += "\nRecorded down payment exceeds current catalogue fee; verify amount and fee."
            if matches:
                notes += f"\nDuplicate phone reviewed and approved as a separate lead; existing CRM lead(s) {sorted(matches)} unchanged."
            created = decision_date(row["Created Time"], decisions["correct_2006_to_2026"])
            closing = decision_date(row["Closing Date"], decisions["correct_2006_to_2026"])
            modified = decision_date(row["Modified Time"], decisions["correct_2006_to_2026"])
            prepared.append({
                "full_name": clean(row["Contact Name"]), "phone": primary or mobile,
                "whatsapp": mobile if mobile_key != primary_key else None,
                "email": None, "source": "whatsapp", "stage": "enrolled", "priority": "medium",
                "interested_course": TARGET, "interested_programme": course["programme_name"],
                "academic_course_id": course["course_id"], "awarding_body": course["awarding_body"],
                "programme_fee": course["programme_fee"], "programme_duration": course["duration"],
                "school": clean(row["School"]) or None, "nationality": clean(row["Nationality"]) or None,
                "faculty": clean(row["Faculty  (Schools)"]) or None,
                "highest_qualification": clean(row["Student Highest Education Qualification"]) or None,
                "city": clean(row["City"]) or None, "country": clean(row["Country"]) or None,
                "amount": amount, "amount_paid": amount, "payment_status": "down_payment",
                "payment_date": closing, "enrolled_at": closing, "created_at": created,
                "updated_at": modified, "assigned_at": created,
                "enrollment_email_status": "pending_email_legacy", "notes": notes,
                "external_record_id": external_id, "is_archived": False,
                "assigned_counsellor_id": owner["user_id"], "counsellor_name": owner["full_name"],
            })
        print(json.dumps({"ready_rows": list(HELD_ROWS), "course": TARGET, "course_id": course["course_id"],
                          "current_course_fee": str(course["programme_fee"]),
                          "matching_crm_leads": {str(k): sorted(v) for k, v in APPROVED_PHONE_MATCHES.items()},
                          "assigned_to": {lead["external_record_id"]: lead["counsellor_name"] for lead in prepared}}, indent=2))
        if not apply:
            print("Preview only; no database changes.")
            return
        created_ids = []
        for values in prepared:
            columns = list(values)
            statement = text(f"INSERT INTO crm_leads ({', '.join(columns)}) VALUES ({', '.join(':'+name for name in columns)}) RETURNING lead_id")
            created_ids.append(await db.scalar(statement, values))
        print(json.dumps({"created_lead_ids": created_ids, "emails_sent": 0}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.source, args.review, args.apply))


if __name__ == "__main__":
    main()
