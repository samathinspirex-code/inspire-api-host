"""Preview or import a Zoho Students Pipeline workbook into the CRM.

The command is a dry run unless ``--apply`` is supplied. Matching is performed
by Zoho Record Id first. For records imported before that field existed, a
unique email or phone match is used once to attach the Zoho identifier.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from openpyxl import load_workbook
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import engine


STAGE_ALIASES = {
    "new inquiry": "new_inquiry",
    "new lead": "new_inquiry",
    "new leads": "new_inquiry",
    "potential lead": "new_inquiry",
    "contacted": "contacted",
    "attempted to contact": "contacted",
    "uncontactable no info": "contacted",
    "counselling": "counselling",
    "counseling": "counselling",
    "interested": "counselling",
    "follow up": "counselling",
    "exploring other options": "counselling",
    "waiting for parental approval": "counselling",
    "application started": "application_started",
    "future prospect": "application_started",
    "documents pending": "documents_pending",
    "looking for results": "documents_pending",
    "application submitted": "app_submitted",
    "app submitted": "app_submitted",
    "offer sent": "offer_sent",
    "ready to enrolled": "offer_sent",
    "payment done": "enrolled",
    "enrolled": "enrolled",
    "not interested declined": "lost_deferred",
    "not interested": "lost_deferred",
    "declined": "lost_deferred",
    "lost": "lost_deferred",
    "deferred": "lost_deferred",
    "junk leads": "lost_deferred",
    "can t afford": "lost_deferred",
    "lost to competitor": "lost_deferred",
}

FIELDS = (
    "full_name",
    "email",
    "phone",
    "whatsapp",
    "city",
    "highest_qualification",
    "interested_programme",
    "interested_course",
    "message",
    "source",
    "stage",
    "priority",
    "counsellor_name",
    "notes",
    "followup_date",
    "amount",
    "school",
    "nationality",
    "faculty",
    "student_status",
    "parents_occupation",
    "parents_email",
    "address_line1",
    "address_line2",
    "country",
    "social_lead_id",
    "external_record_id",
)


def clean(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    result = str(value).strip()
    return result or None


def normalize_words(value: Any) -> str:
    result = clean(value) or ""
    result = re.sub(r"[_/+-]+", " ", result.lower())
    return re.sub(r"[^a-z0-9]+", " ", result).strip()


def normalize_email(value: Any) -> str | None:
    result = (clean(value) or "").lower()
    return result if "@" in result else None


def normalize_phone(value: Any) -> str | None:
    digits = re.sub(r"\D", "", clean(value) or "")
    if len(digits) < 7:
        return None
    # Treat 07x, +947x, and 947x as the same Sri Lankan number.
    return digits[-9:] if len(digits) >= 9 else digits


def parse_amount(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    raw = clean(value)
    if not raw:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(raw[:19], fmt)
        except ValueError:
            continue
    return None


def mapped_stage(stage: Any, student_status: Any) -> tuple[str, bool]:
    raw = normalize_words(stage)
    if raw in STAGE_ALIASES:
        return STAGE_ALIASES[raw], True
    status = normalize_words(student_status)
    if status in STAGE_ALIASES:
        return STAGE_ALIASES[status], True
    if "payment" in status or "enrol" in status:
        return "enrolled", True
    if any(token in status for token in ("not interested", "declin", "defer")):
        return "lost_deferred", True
    return "new_inquiry", not bool(raw)


def cell(row: tuple[Any, ...], indexes: dict[str, int], name: str) -> Any:
    index = indexes.get(name)
    return row[index] if index is not None and index < len(row) else None


def first_name_key(value: str | None) -> str | None:
    words = normalize_words(value)
    if not words:
        return None
    first = words.split()[0]
    return {"jeniffer": "jennifer", "jenniffer": "jennifer"}.get(first, first)


def richness(record: dict[str, Any]) -> int:
    return sum(1 for field in FIELDS if record.get(field) not in (None, ""))


def record_from_getter(
    get: Callable[[str], Any],
    row_number: int,
    unknown_stages: Counter[str],
) -> dict[str, Any] | None:
    full_name = clean(get("Students Pipeline Name")) or clean(get("Contact Name"))
    external_id = clean(get("Record Id"))
    if not full_name or not external_id:
        return None
    raw_stage = clean(get("Stage"))
    student_status = clean(get("Student Status"))
    stage, recognized = mapped_stage(raw_stage, student_status)
    if not recognized:
        unknown_stages[raw_stage or "(blank)"] += 1
    email = normalize_email(get("Email") or get("Email 1"))
    phone_raw = clean(get("Phone")) or clean(get("Phone 1")) or clean(get("Mobile"))
    mobile_raw = clean(get("Mobile")) or phone_raw
    reason = clean(get("Reason For Loss"))
    source_raw = clean(get("Lead Source")) or "zoho_import"
    source = re.sub(r"[^a-z0-9]+", "_", source_raw.lower()).strip("_")
    priority = "high" if stage in {"offer_sent", "enrolled"} else "medium"
    return {
        "_row": row_number,
        "_email_key": email,
        "_phone_key": normalize_phone(phone_raw),
        "full_name": full_name,
        "email": email,
        "phone": phone_raw,
        "whatsapp": mobile_raw,
        "city": clean(get("City")),
        "highest_qualification": clean(get("Student Highest Education Qualification")),
        "interested_programme": clean(get("Faculty  (Schools)")),
        "interested_course": clean(get("Selected Program")),
        "message": clean(get("Description")),
        "source": source or "zoho_import",
        "stage": stage,
        "priority": priority,
        "counsellor_name": clean(get("Students Pipeline Owner")),
        "notes": f"Reason for loss: {reason}" if reason else None,
        "followup_date": parse_datetime(get("Closing Date")),
        "amount": parse_amount(get("Amount")),
        "school": clean(get("School")),
        "nationality": clean(get("Nationality")),
        "faculty": clean(get("Faculty  (Schools)")),
        "student_status": student_status,
        "parents_occupation": clean(get("Parents Occupation")),
        "parents_email": normalize_email(get("Parents Email")),
        "address_line1": clean(get("Address Line 1")),
        "address_line2": clean(get("Address Line 2")),
        "country": clean(get("Country")),
        "social_lead_id": clean(get("Social Lead ID")),
        "external_record_id": external_id,
        "_created_at": parse_datetime(get("Created Time")),
        "_updated_at": parse_datetime(get("Modified Time")),
    }


def read_workbook(path: Path) -> tuple[list[dict[str, Any]], Counter[str]]:
    sheet = load_workbook(path, read_only=True, data_only=True).active
    headers = [clean(item.value) for item in next(sheet.iter_rows())]
    indexes = {name: index for index, name in enumerate(headers) if name}
    required = {"Record Id", "Students Pipeline Name", "Stage"}
    missing = required.difference(indexes)
    if missing:
        raise ValueError(f"{path.name} is missing required columns: {sorted(missing)}")

    records: list[dict[str, Any]] = []
    unknown_stages: Counter[str] = Counter()
    for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        record = record_from_getter(lambda name, r=row: cell(r, indexes, name), row_number, unknown_stages)
        if record:
            records.append(record)
    return records, unknown_stages


def read_csv_file(path: Path) -> tuple[list[dict[str, Any]], Counter[str]]:
    records: list[dict[str, Any]] = []
    unknown_stages: Counter[str] = Counter()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path.name} has no header row")
        required = {"Record Id", "Students Pipeline Name", "Stage"}
        missing = required.difference(set(reader.fieldnames))
        if missing:
            raise ValueError(f"{path.name} is missing required columns: {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            record = record_from_getter(lambda name, current=row: current.get(name), row_number, unknown_stages)
            if record:
                records.append(record)
    return records, unknown_stages


def read_source(path: Path) -> tuple[list[dict[str, Any]], Counter[str]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return read_csv_file(path)
    if suffix in {".xlsx", ".xlsm"}:
        return read_workbook(path)
    raise ValueError(f"Unsupported file type: {path.name}")


def merge_records(paths: list[Path]) -> tuple[list[dict[str, Any]], Counter[str]]:
    merged: dict[str, dict[str, Any]] = {}
    unknown_stages: Counter[str] = Counter()
    for path in paths:
        records, file_unknown = read_source(path)
        unknown_stages.update(file_unknown)
        print(f"Read {len(records):,} rows from {path.name}")
        for record in records:
            key = record["external_record_id"]
            current = merged.get(key)
            if current is None or richness(record) >= richness(current):
                merged[key] = record
    return list(merged.values()), unknown_stages


def batched(items: list[dict[str, Any]], size: int = 400):
    for index in range(0, len(items), size):
        yield items[index : index + size]


async def column_exists(connection: Any) -> bool:
    result = await connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='crm_leads' "
            "AND column_name='external_record_id')"
        )
    )
    return bool(result)


def build_lookup(rows: list[dict[str, Any]], field: str) -> dict[str, list[int]]:
    result: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        value = row.get(field)
        if value:
            result[value].append(row["lead_id"])
    return result


async def load_counsellor_index(connection: Any) -> dict[str, dict[str, Any]]:
    rows = (
        await connection.execute(
            text(
                """
                SELECT u.user_id, u.full_name,
                       bool_or(al.access_key = 'COUNSELLOR') AS is_counsellor
                FROM users u
                JOIN user_access_levels ual ON ual.user_id = u.user_id
                JOIN access_levels al ON al.access_level_id = ual.access_level_id
                WHERE u.is_active = true
                  AND al.is_active = true
                  AND al.access_key IN ('COUNSELLOR', 'CRM')
                GROUP BY u.user_id, u.full_name
                ORDER BY bool_or(al.access_key = 'COUNSELLOR') DESC, u.user_id
                """
            )
        )
    ).mappings().all()
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = first_name_key(row["full_name"])
        if not key:
            continue
        current = index.get(key)
        if current is None or (row["is_counsellor"] and not current["is_counsellor"]):
            index[key] = dict(row)
    return index


def resolve_counsellor(
    name: str | None, index: dict[str, dict[str, Any]]
) -> tuple[int | None, str | None]:
    key = first_name_key(name)
    if not key:
        return None, name
    match = index.get(key)
    if match:
        return match["user_id"], match["full_name"]
    return None, name


async def run(paths: list[Path], apply: bool) -> None:
    records, unknown_stages = merge_records(paths)
    workbook_email_counts = Counter(row["_email_key"] for row in records if row["_email_key"])
    workbook_phone_counts = Counter(row["_phone_key"] for row in records if row["_phone_key"])

    async with engine.begin() as connection:
        has_external_id = await column_exists(connection)
        if apply and not has_external_id:
            raise RuntimeError(
                "Run scripts/apply_crm_external_record_id_migration.py before --apply."
            )
        counsellor_index = await load_counsellor_index(connection)
        external_select = "external_record_id" if has_external_id else "NULL AS external_record_id"
        existing_result = await connection.execute(
            text(
                "SELECT lead_id, lower(email) AS email_key, phone, "
                "assigned_counsellor_id, counsellor_name, "
                f"{external_select} FROM crm_leads"
            )
        )
        existing = []
        for item in existing_result.mappings():
            existing.append(
                {
                    "lead_id": item["lead_id"],
                    "email_key": normalize_email(item["email_key"]),
                    "phone_key": normalize_phone(item["phone"]),
                    "external_record_id": clean(item["external_record_id"]),
                    "assigned_counsellor_id": item["assigned_counsellor_id"],
                    "counsellor_name": item["counsellor_name"],
                }
            )

        by_external = {
            row["external_record_id"]: row["lead_id"]
            for row in existing
            if row["external_record_id"]
        }
        unlinked = [row for row in existing if not row["external_record_id"]]
        by_email = build_lookup(unlinked, "email_key")
        by_phone = build_lookup(unlinked, "phone_key")
        existing_by_id = {row["lead_id"]: row for row in existing}

        creates: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        conflicts: list[tuple[int, str]] = []
        matched_external = matched_email = matched_phone = 0
        claimed_existing: set[int] = set()

        for record in records:
            user_id, official_name = resolve_counsellor(record["counsellor_name"], counsellor_index)
            if official_name:
                record["counsellor_name"] = official_name
            record["assigned_counsellor_id"] = user_id

            lead_id = by_external.get(record["external_record_id"])
            match_kind = "external"
            if lead_id is None:
                candidates: set[int] = set()
                email_key = record["_email_key"]
                phone_key = record["_phone_key"]
                if email_key:
                    email_matches = [
                        lead_id
                        for lead_id in by_email.get(email_key, [])
                        if lead_id not in claimed_existing
                    ]
                    if len(email_matches) == 1 and (
                        workbook_email_counts[email_key] == 1 or not record["_phone_key"]
                    ):
                        candidates.add(email_matches[0])
                if phone_key:
                    phone_matches = [
                        lead_id
                        for lead_id in by_phone.get(phone_key, [])
                        if lead_id not in claimed_existing
                    ]
                    if len(phone_matches) == 1:
                        candidates.add(phone_matches[0])
                if len(candidates) > 1:
                    conflicts.append((record["_row"], "email and phone match different CRM records"))
                    continue
                if candidates:
                    lead_id = candidates.pop()
                    match_kind = "email" if email_key and lead_id in by_email.get(email_key, []) else "phone"
            values = {field: record.get(field) for field in FIELDS}
            values["assigned_counsellor_id"] = record["assigned_counsellor_id"]
            if lead_id is None:
                values["created_at"] = record["_created_at"] or datetime.now()
                values["updated_at"] = record["_updated_at"] or values["created_at"]
                creates.append(values)
            elif lead_id in claimed_existing and match_kind != "external":
                conflicts.append((record["_row"], "fallback match was already claimed"))
            else:
                claimed_existing.add(lead_id)
                current = existing_by_id[lead_id]
                if current["assigned_counsellor_id"] and match_kind != "external":
                    values["assigned_counsellor_id"] = current["assigned_counsellor_id"]
                    values["counsellor_name"] = current["counsellor_name"] or values["counsellor_name"]
                values["lead_id"] = lead_id
                values["import_updated_at"] = record["_updated_at"] or datetime.now()
                updates.append(values)
                if match_kind == "external":
                    matched_external += 1
                elif match_kind == "email":
                    matched_email += 1
                else:
                    matched_phone += 1

        print(f"Unique Zoho records ready: {len(records):,}")
        print(f"Existing CRM leads: {len(existing):,}")
        print(f"Would create: {len(creates):,}")
        print(f"Would update: {len(updates):,}")
        print(
            "Matches: "
            f"Zoho ID={matched_external:,}, email={matched_email:,}, phone={matched_phone:,}"
        )
        print(f"Conflicts skipped: {len(conflicts):,}")
        if unknown_stages:
            print(f"Unrecognized stage rows defaulted to new inquiry: {sum(unknown_stages.values()):,}")
            print("Unrecognized stages:", unknown_stages.most_common(20))
        if conflicts:
            print("First conflicts:", conflicts[:10])

        if not apply:
            print("Dry run only; no CRM records were changed.")
            return

        insert_fields = list(FIELDS) + ["assigned_counsellor_id", "created_at", "updated_at"]
        insert_columns = ", ".join(insert_fields)
        insert_values = ", ".join(f":{field}" for field in insert_fields)
        for batch in batched(creates):
            await connection.execute(
                text(f"INSERT INTO crm_leads ({insert_columns}) VALUES ({insert_values})"),
                batch,
            )

        assignments = ", ".join(
            f"{field} = COALESCE(:{field}, {field})" for field in FIELDS
        )
        for batch in batched(updates):
            await connection.execute(
                text(
                    f"UPDATE crm_leads SET {assignments}, "
                    "assigned_counsellor_id = COALESCE(:assigned_counsellor_id, assigned_counsellor_id), "
                    "updated_at = :import_updated_at WHERE lead_id = :lead_id"
                ),
                batch,
            )
        print(f"Applied successfully: {len(creates):,} created, {len(updates):,} updated.")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run([path.resolve() for path in args.files], args.apply))


if __name__ == "__main__":
    main()
