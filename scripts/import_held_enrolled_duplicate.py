"""Import row 45 as the single approved lead for duplicate rows 43/45."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import engine
from scripts.import_enrolled_additions import (
    SOURCE_SHA256, COURSE_NAMES, get_decisions, numbers, read_rows, same_original_rows,
)
from scripts.import_enrolled_workbook import HEADERS, clean, key, phone


async def run(source: Path, original: Path, review: Path, apply: bool) -> None:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError("Source workbook changed since review")
    decisions = get_decisions(review)
    if decisions["duplicate_rows_43_45"] != "same_import_45" or decisions["shenoli_identity"] != "existing_user_22":
        raise ValueError("Review decisions do not authorize this import")
    rows = read_rows(source)
    same_original_rows(rows, original)
    duplicate, chosen = rows[41], rows[43]
    if (duplicate["_row"], chosen["_row"]) != (43, 45):
        raise ValueError("Duplicate row positions changed")
    for field in ("Phone", "Mobile", "Created Time", "Closing Date", "Selected Program", "Created By", "Amount", "Status"):
        if duplicate[field] != chosen[field]:
            raise ValueError(f"Rows 43 and 45 differ in {field}; review again")
    if key(chosen["Status"]) != "payment done" or key(chosen["Created By"]) != "shenoli chandrawansa" or clean(chosen["Email"]):
        raise ValueError("Unexpected row 45 status, creator, or email")
    target = COURSE_NAMES.get(key(chosen["Selected Program"]))
    if target != "HND in Computing (Software Engineering)":
        raise ValueError("Unexpected course for row 45")
    parsed = numbers(chosen["Phone"]) + numbers(chosen["Mobile"])
    unique = list(dict(parsed).items())
    if len(unique) != 1:
        raise ValueError("Expected one shared contact number")
    normalized, display_phone = unique[0]
    if not isinstance(chosen["Created Time"], datetime) or chosen["Created Time"].year != 2026:
        raise ValueError("Unexpected creation date")
    if clean(chosen["Amount"]) != "400000":
        raise ValueError("Unexpected workbook amount")
    prefix = f"enrolled-additions-{digest[:12]}-row-"
    async with engine.begin() as db:
        existing = (await db.execute(text("""
            SELECT lead_id, phone, whatsapp, external_record_id FROM crm_leads
            WHERE external_record_id LIKE :prefix OR phone=:phone OR whatsapp=:phone
        """), {"prefix": f"{prefix}%", "phone": display_phone})).mappings().all()
        imported_rows = {int(lead["external_record_id"].removeprefix(prefix)) for lead in existing if lead["external_record_id"] and lead["external_record_id"].startswith(prefix)}
        if imported_rows != set(range(35, 62)) - {43, 45}:
            raise ValueError(f"Previously imported row set changed: {sorted(imported_rows)}")
        matches = set()
        for lead in existing:
            for field in ("phone", "whatsapp"):
                number, _ = phone(lead[field])
                if number == normalized:
                    matches.add(lead["lead_id"])
        if matches:
            raise ValueError(f"The duplicate number now matches CRM lead(s): {sorted(matches)}")
        course = (await db.execute(text("""
            SELECT c.course_id, c.title, c.awarding_body, p.name AS programme_name,
                   o.price AS programme_fee, o.duration
            FROM academic_courses c JOIN academic_programmes p ON p.programme_id=c.programme_id
            JOIN academic_schools s ON s.school_id=c.school_id
            LEFT JOIN LATERAL (
                SELECT price, duration FROM academic_course_study_options
                WHERE course_id=c.course_id AND is_enabled=true
                ORDER BY price, study_option_id LIMIT 1
            ) o ON true
            WHERE c.title=:title AND c.status='active' AND p.status='active' AND s.status='active'
        """), {"title": target})).mappings().one()
        owner = (await db.execute(text("""
            SELECT u.user_id, u.full_name, u.is_active,
                   coalesce(bool_or(al.access_key='COUNSELLOR'),false) AS is_counsellor
            FROM users u LEFT JOIN user_access_levels ua ON ua.user_id=u.user_id
            LEFT JOIN access_levels al ON al.access_level_id=ua.access_level_id
            WHERE u.user_id=22 GROUP BY u.user_id,u.full_name,u.is_active
        """))).mappings().one()
        if not owner["is_active"] or not owner["is_counsellor"] or key(owner["full_name"]) != "shenoli":
            raise ValueError("Approved Shenoli account changed")
        print(json.dumps({"import_row": 45, "omit_duplicate_row": 43, "name": clean(chosen["Contact Name"]),
                          "phone": display_phone, "course": target, "owner": owner["full_name"],
                          "created_at": str(chosen["Created Time"]), "amount": clean(chosen["Amount"])}, indent=2))
        if not apply:
            print("Preview only; no database changes.")
            return
        audit = {field: clean(chosen[field]) for field in HEADERS}
        duplicate_audit = {field: clean(duplicate[field]) for field in HEADERS}
        notes = f"Legacy import: Enrolled (1).xlsx, row 45. Original values: {json.dumps(audit, ensure_ascii=False)}"
        notes += f"\nWorkbook row 43 was reviewed as the same student and not imported. Row 43 original values: {json.dumps(duplicate_audit, ensure_ascii=False)}"
        notes += "\nLegacy Enrolled exception: email pending; do not send offer or registration email until a valid address is supplied."
        notes += "\nWorkbook Amount is a down payment, not the full programme fee."
        notes += "\nContact number retained from workbook; verify its unusual format."
        if course["programme_fee"] is not None and 400000 > float(course["programme_fee"]):
            notes += "\nRecorded down payment exceeds current catalogue fee; verify amount and fee."
        values = {
            "full_name": clean(chosen["Contact Name"]), "phone": display_phone,
            "whatsapp": None, "email": None, "source": "whatsapp", "stage": "enrolled", "priority": "medium",
            "interested_course": target, "interested_programme": course["programme_name"],
            "academic_course_id": course["course_id"], "awarding_body": course["awarding_body"],
            "programme_fee": course["programme_fee"], "programme_duration": course["duration"],
            "school": clean(chosen["School"]) or None, "nationality": clean(chosen["Nationality"]) or None,
            "faculty": clean(chosen["Faculty  (Schools)"]) or None,
            "highest_qualification": clean(chosen["Student Highest Education Qualification"]) or None,
            "city": clean(chosen["City"]) or None, "country": clean(chosen["Country"]) or None,
            "amount": 400000, "amount_paid": 400000, "payment_status": "down_payment",
            "payment_date": chosen["Closing Date"], "enrolled_at": chosen["Closing Date"],
            "created_at": chosen["Created Time"],
            "updated_at": chosen["Modified Time"] if isinstance(chosen["Modified Time"], datetime) else chosen["Created Time"],
            "assigned_at": chosen["Created Time"], "enrollment_email_status": "pending_email_legacy",
            "notes": notes, "external_record_id": f"{prefix}45", "is_archived": False,
            "assigned_counsellor_id": owner["user_id"], "counsellor_name": owner["full_name"],
        }
        columns = list(values)
        statement = text(f"INSERT INTO crm_leads ({', '.join(columns)}) VALUES ({', '.join(':'+column for column in columns)}) RETURNING lead_id")
        lead_id = await db.scalar(statement, values)
        print(json.dumps({"created_lead_id": lead_id, "omitted_duplicate_row": 43, "emails_sent": 0}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.source, args.original, args.review, args.apply))


if __name__ == "__main__":
    main()
