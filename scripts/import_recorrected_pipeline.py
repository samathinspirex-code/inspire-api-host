"""Preview or import the reviewed Recorrected pipeline workbook into CRM.

Read-only by default. ``--apply`` creates missing counsellor profiles and leads
in one database transaction. The source workbook and reviewed mapping file are
passed explicitly so a later run cannot silently use different input files.
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


NEW_COUNSELLORS = {
    "samindi weerasiri",
    "subani palandage",
    "fathima ziniya ahsan",
    "diunika jayasinghe",
    "thehansa abeykoon",
    "dinushi inspirex",
    "rehan",
    "oshini perera",
    "michelle rebecca",
    "dinushi thakshila",
}
COUNSELLOR_ALIASES = {
    "jennifer mishel fernando": "jennifer",
    "ameera": "ameera zanhar",
}
HEADERS = (
    "Contact Name", "Created Time", "Lead Source", "Selected Program",
    "Status", "Phone", "Mobile", "Closing Date", "Created By",
)


def clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean(value).lower()).strip()


def phone_parts(value: Any) -> tuple[str | None, str | None, bool]:
    """Return dedupe key, display number, and whether it needs correction."""
    raw = clean(value)
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None, None, False
    if len(digits) == 10 and digits.startswith("0"):
        return f"94{digits[1:]}", f"+94{digits[1:]}", False
    if len(digits) == 11 and digits.startswith("94"):
        return digits, f"+{digits}", False
    if len(digits) == 9 and digits.startswith("7"):
        return f"94{digits}", f"+94{digits}", False
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return digits, f"+{digits}", False
    if digits.startswith("00") and 10 <= len(digits) <= 17:
        return digits[2:], f"+{digits[2:]}", False
    if 7 <= len(digits) <= 15:
        return f"raw:{digits}", raw, True
    return None, None, True


def phone_like_name(value: Any) -> bool:
    raw = clean(value)
    return bool(re.fullmatch(r"[+\d\s()\-./]+", raw) and len(re.sub(r"\D", "", raw)) >= 7)


def parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.fromisoformat(clean(value))


def source_key(value: Any) -> str:
    source = key(value)
    if "whatsapp" in source:
        return "whatsapp"
    if "instagram" in source:
        return "instagram"
    if "referral" in source:
        return "referral"
    if "lead form" in source or "facebook" in source or "messenger" in source:
        return "meta"
    if "direct registration" in source:
        return "website_admission"
    if "website" in source:
        return "website_contact"
    if source in {"chat", "webchat"}:
        return "webchat"
    return source.replace(" ", "_")[:50] or "legacy_import"


def stage_fields(raw_status: str) -> dict[str, Any]:
    status = key(raw_status)
    result: dict[str, Any] = {
        "stage": "new_lead", "status_reason": None,
        "status_remarks": None, "affordability_reason": None,
        "delay_reason": None, "enrollment_email_status": None,
    }
    if status == "uncontactable no info":
        result["stage"] = "uncontactable"
    elif status in {"not interested declined", "junk leads"}:
        result.update(stage="not_interested", status_reason="Other", status_remarks=raw_status)
    elif status == "lost to competitor":
        result.update(stage="lost_to_competitor", status_reason="Other", status_remarks=raw_status)
    elif status == "can t afford":
        result.update(stage="cant_afford", affordability_reason="Other", status_remarks=raw_status)
    elif status == "looking for results":
        result.update(stage="future_prospect", delay_reason="Waiting for Exam Results")
    elif status == "waiting for parental approval":
        result.update(stage="future_prospect", delay_reason="Need Parent / Sponsor Approval")
    elif status in {"exploring other options", "future prospect", "ready to enrolled"}:
        result.update(stage="future_prospect", delay_reason="Other", status_remarks=raw_status)
    elif status == "payment done":
        result.update(stage="enrolled", enrollment_email_status="pending_email_legacy")
    elif status != "potential lead":
        raise ValueError(f"Unmapped workbook status: {raw_status!r}")
    return result


def mapped_course(value: str) -> tuple[str, str | None]:
    """Treat sheet column C as the user's chosen name; strip workflow annotations."""
    target = clean(value).replace("\ufffd", "–")
    if re.search(r"\(\s*need add to website\s*\)", target, re.IGNORECASE):
        return re.sub(r"\s*\(\s*need add to website\s*\)", "", target, flags=re.IGNORECASE).strip(" -"), "Need add to website"
    if "unav" in target.lower():
        base = re.sub(r"\s*\([^)]*\)\s*$", "", target).strip()
        return f"{base} (unavailable)", "Unavailable course"
    return target, None


def read_mapping(path: Path) -> dict[str, tuple[str, str | None]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    if "Programme mapping" not in workbook.sheetnames:
        raise ValueError("Reviewed workbook has no Programme mapping sheet")
    mapping: dict[str, tuple[str, str | None]] = {}
    for row in workbook["Programme mapping"].iter_rows(min_row=4, values_only=True):
        original = clean(row[0])
        if not original:
            continue
        chosen = clean(row[4]) or clean(row[2])
        if not chosen:
            raise ValueError(f"No chosen mapping for {original!r}")
        normalized = key(original)
        value = mapped_course(chosen)
        if normalized in mapping and mapping[normalized] != value:
            raise ValueError(f"Conflicting mappings for {original!r}")
        mapping[normalized] = value
    return mapping


def read_source(path: Path) -> list[dict[str, Any]]:
    worksheet = load_workbook(path, read_only=True, data_only=True).active
    header = tuple(clean(cell) for cell in next(worksheet.iter_rows(values_only=True)))
    if header != HEADERS:
        raise ValueError(f"Unexpected workbook columns: {header!r}")
    rows = []
    for number, values in enumerate(worksheet.iter_rows(min_row=2, values_only=True), 2):
        if not any(clean(value) for value in values):
            continue
        row = dict(zip(HEADERS, values))
        row["_row"] = number
        row["_created"] = parse_time(row["Created Time"])
        row["_phone_keys"] = set()
        row["_phones"] = {}
        for field in ("Phone", "Mobile"):
            part = phone_parts(row[field])
            row["_phones"][field] = part
            if part[0]:
                row["_phone_keys"].add(part[0])
        if phone_like_name(row["Contact Name"]):
            part = phone_parts(row["Contact Name"])
            row["_phones"]["Contact Name"] = part
            if part[0]:
                row["_phone_keys"].add(part[0])
        rows.append(row)
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
        for phone in row["_phone_keys"]:
            if phone in seen:
                parent[find(index)] = find(seen[phone])
            else:
                seen[phone] = index
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[find(index)].append(row)
    return list(groups.values())


def owner_key(value: Any) -> str:
    normalized = key(value)
    return COUNSELLOR_ALIASES.get(normalized, normalized)


def prepare_group(
    group: list[dict[str, Any]], mapping: dict[str, tuple[str, str | None]],
    courses: dict[str, dict[str, Any]], source_hash: str,
) -> dict[str, Any]:
    ordered = sorted(group, key=lambda row: (row["_created"], row["_row"]))
    latest = ordered[-1]
    status_row = next((row for row in reversed(ordered) if key(row["Status"]) == "payment done"), latest)
    programme_row = next((row for row in reversed(ordered) if clean(row["Selected Program"])), None)
    name_row = next((row for row in reversed(ordered) if clean(row["Contact Name"])), latest)
    phone_row = next((row for row in reversed(ordered) if any(row["_phones"].get(f, (None,))[0] for f in ("Phone", "Mobile", "Contact Name"))), latest)
    primary = next((phone_row["_phones"].get(field, (None, None, False))[1] for field in ("Phone", "Mobile", "Contact Name") if phone_row["_phones"].get(field, (None,))[0]), None)
    mobile = latest["_phones"].get("Mobile", (None, None, False))[1]
    raw_programme = clean(programme_row["Selected Program"]) if programme_row else ""
    mapped, marker = mapping.get(key(raw_programme), (raw_programme, None)) if raw_programme else ("", None)
    choice = courses.get(key(mapped)) if mapped else None
    fields = stage_fields(clean(status_row["Status"]))
    owner = owner_key(latest["Created By"])
    audit_rows = [{
        "row": row["_row"], "contact_name": clean(row["Contact Name"]),
        "created_time": clean(row["Created Time"]), "source": clean(row["Lead Source"]),
        "programme": clean(row["Selected Program"]), "status": clean(row["Status"]),
        "phone": clean(row["Phone"]), "mobile": clean(row["Mobile"]),
        "closing_date": clean(row["Closing Date"]), "created_by": clean(row["Created By"]),
    } for row in ordered]
    notes = "Legacy import: Recorrected pipeline.xlsx. Original workbook rows: " + json.dumps(audit_rows, ensure_ascii=False)
    if marker:
        notes += f"\nProgramme mapping note: {marker}."
    if fields["stage"] == "enrolled":
        notes += "\nLegacy Enrolled exception: email pending; do not send offer or registration email until a valid address is supplied."
    if any(part[2] for row in ordered for part in row["_phones"].values()):
        notes += "\nOne or more source phone values need correction."
    if not any(row["_phone_keys"] for row in ordered):
        notes += "\nNo usable phone number in source; contact number pending correction."
    return {
        "full_name": clean(name_row["Contact Name"]) or primary or f"Imported lead row {ordered[0]['_row']}",
        "email": None, "phone": primary, "whatsapp": mobile,
        "interested_course": mapped or None,
        "interested_programme": choice["programme_name"] if choice else None,
        "academic_course_id": choice["course_id"] if choice else None,
        "awarding_body": choice["awarding_body"] if choice else None,
        "programme_fee": choice["programme_fee"] if choice else None,
        "programme_duration": choice["duration"] if choice else None,
        "source": source_key(ordered[0]["Lead Source"]),
        "priority": "medium", "notes": notes, "followup_date": None,
        "enrolled_at": status_row["_created"] if fields["stage"] == "enrolled" else None,
        "created_at": ordered[0]["_created"], "updated_at": latest["_created"],
        "assigned_at": ordered[0]["_created"],
        "external_record_id": f"recorrected-{source_hash}-row-{ordered[0]['_row']}",
        "_owner_key": owner, "_phone_keys": set().union(*(row["_phone_keys"] for row in ordered)),
        "_source_rows": [row["_row"] for row in ordered],
        **fields,
    }


async def run(source: Path, review: Path, apply: bool) -> None:
    mapping = read_mapping(review)
    rows = read_source(source)
    if len(rows) != 7889:
        raise ValueError(f"Expected 7,889 source rows, found {len(rows):,}")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    source_programmes = {key(row["Selected Program"]) for row in rows if clean(row["Selected Program"])}

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
        courses = {key(row["title"]): dict(row) for row in catalogue}
        unmapped = source_programmes - set(mapping) - set(courses)
        if unmapped:
            raise ValueError(f"Programme mappings missing: {sorted(unmapped)}")
        if len(mapping) != 81:
            raise ValueError(f"Expected 81 reviewed programme mappings, found {len(mapping)}")
        existing_users = (await db.execute(text("""
            SELECT u.user_id, u.full_name, u.email, u.is_active,
                   bool_or(al.access_key='COUNSELLOR') AS is_counsellor,
                   array_remove(array_agg(al.access_key), NULL) AS roles
            FROM users u LEFT JOIN user_access_levels ual ON ual.user_id=u.user_id
            LEFT JOIN access_levels al ON al.access_level_id=ual.access_level_id
            GROUP BY u.user_id, u.full_name, u.email, u.is_active
        """))).mappings().all()
        users = {key(row["full_name"]): dict(row) for row in existing_users if row["full_name"]}
        new_names = {key(row["Created By"]): clean(row["Created By"]) for row in rows if owner_key(row["Created By"]) not in users}
        if not set(new_names).issubset(NEW_COUNSELLORS):
            raise ValueError(f"Unexpected unmatched counsellors: {new_names}")
        existing_leads = (await db.execute(text("SELECT phone, whatsapp, external_record_id FROM crm_leads"))).mappings().all()
        existing_phone_keys = {part[0] for lead in existing_leads for field in ("phone", "whatsapp") if (part := phone_parts(lead[field]))[0]}
        existing_external = {lead["external_record_id"] for lead in existing_leads if lead["external_record_id"]}

        groups = group_rows(rows)
        prepared = [prepare_group(group, mapping, courses, source_hash) for group in groups]
        no_number = [lead for lead in prepared if not lead["_phone_keys"]]
        duplicates = [lead for lead in prepared if lead["_phone_keys"] & existing_phone_keys or lead["external_record_id"] in existing_external]
        excluded_ids = {id(lead) for lead in duplicates}
        ready = [lead for lead in prepared if id(lead) not in excluded_ids]
        counts = Counter(lead["stage"] for lead in ready)
        print(json.dumps({
            "source_rows": len(rows), "source_sha256_prefix": source_hash,
            "reviewed_programmes": len(mapping), "new_counsellor_profiles": new_names,
            "unique_phone_groups": len(groups), "collapsed_duplicate_rows": len(rows) - len(groups),
            "existing_crm_duplicate_groups_skipped": len(duplicates),
            "groups_without_usable_number_imported": len(no_number),
            "leads_ready_to_create": len(ready), "ready_stages": dict(counts),
            "ready_by_owner": dict(Counter(lead["_owner_key"] for lead in ready)),
            "existing_owner_access": {
                owner_key(name): {"name": users[owner_key(name)]["full_name"], "is_active": users[owner_key(name)]["is_active"], "roles": users[owner_key(name)]["roles"]}
                for name in {row["Created By"] for row in rows}
                if owner_key(name) in users
            },
            "website_course_label_rows": sum("Need add to website" in lead["notes"] for lead in ready),
            "unavailable_course_label_rows": sum("Unavailable course" in lead["notes"] for lead in ready),
            "linked_to_active_catalogue": sum(lead["academic_course_id"] is not None for lead in ready),
        }, indent=2, default=str))
        if not apply:
            print("Dry run only. No users or leads were changed.")
            return

        access_id = await db.scalar(text("SELECT access_level_id FROM access_levels WHERE access_key='COUNSELLOR' AND is_active=true"))
        if access_id is None:
            raise RuntimeError("COUNSELLOR access level is missing")
        for normalized, display in sorted(new_names.items()):
            email = re.sub(r"[^a-z0-9]+", ".", normalized).strip(".") + ".legacy@example.com"
            conflict = await db.scalar(text("SELECT user_id FROM users WHERE lower(email)=:email"), {"email": email})
            if conflict:
                raise ValueError(f"Placeholder email already exists: {email}")
            user_id = await db.scalar(text("""
                INSERT INTO users (full_name, email, is_active) VALUES (:name, :email, true)
                RETURNING user_id
            """), {"name": display, "email": email})
            await db.execute(text("INSERT INTO user_access_levels (user_id, access_level_id) VALUES (:uid, :aid)"), {"uid": user_id, "aid": access_id})
            # Historical assignments are visible in the roster, but dummy-mail
            # accounts must not receive newly arriving leads in the rotation.
            await db.execute(text("INSERT INTO crm_counsellor_status (user_id, is_active, assign_order) VALUES (:uid, false, 9999)"), {"uid": user_id})
            users[normalized] = {"user_id": user_id, "full_name": display, "is_active": True, "is_counsellor": True}

        columns = [
            "full_name", "email", "phone", "whatsapp", "interested_course",
            "interested_programme", "academic_course_id", "awarding_body",
            "programme_fee", "programme_duration", "source", "stage",
            "status_reason", "status_remarks", "affordability_reason",
            "delay_reason", "enrollment_email_status", "priority", "notes",
            "followup_date", "enrolled_at", "created_at", "updated_at",
            "assigned_at", "assigned_counsellor_id", "counsellor_name",
            "external_record_id", "is_archived",
        ]
        statement = text(f"INSERT INTO crm_leads ({', '.join(columns)}) VALUES ({', '.join(':'+column for column in columns)})")
        payload = []
        for lead in ready:
            owner = users[lead["_owner_key"]]
            # Historical ownership may belong to a counsellor whose account
            # has since been deactivated. Keep that attribution without
            # reactivating the account or adding it to auto assignment.
            if not owner["is_counsellor"]:
                raise ValueError(f"Owner does not have counsellor access: {owner['full_name']}")
            values = {column: lead.get(column) for column in columns}
            values.update(assigned_counsellor_id=owner["user_id"], counsellor_name=owner["full_name"], is_archived=False)
            payload.append(values)
        for offset in range(0, len(payload), 300):
            await db.execute(statement, payload[offset:offset + 300])
        print(f"Applied {len(payload):,} leads and {len(new_names)} new counsellor profiles.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.source, args.review, args.apply))


if __name__ == "__main__":
    main()
