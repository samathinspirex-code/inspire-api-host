"""Build a read-only reconciliation of Corrected CRM.xlsx against CRM."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import engine
from scripts.import_corrected_crm import (
    SOURCE_SHA256, OWNER_ALIASES, course_for, group_rows, load_decisions, read_source,
)
from scripts.import_enrolled_workbook import clean, key, phone
from scripts.import_recorrected_pipeline import stage_fields


async def build(source: Path, review: Path, output: Path) -> None:
    decisions = load_decisions(review)
    source_bytes = source.read_bytes()
    import hashlib
    if hashlib.sha256(source_bytes).hexdigest() != SOURCE_SHA256:
        raise ValueError("The source workbook changed since import")
    rows = read_source(source_bytes, decisions)
    groups = group_rows(rows)
    prefix = f"corrected-crm-{SOURCE_SHA256[:12]}-row-"
    engine.echo = False
    async with engine.connect() as db:
        leads = [dict(row) for row in (await db.execute(text("""
            SELECT lead_id, full_name, phone, whatsapp, stage, interested_course,
                   academic_course_id, counsellor_name, assigned_counsellor_id,
                   created_at, external_record_id FROM crm_leads
        """))).mappings()]
        catalogue = {row["course_id"]: dict(row) for row in (await db.execute(text("""
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
        """))).mappings()}
    by_title = {key(item["title"]): item["course_id"] for item in catalogue.values()}
    prior_by_phone: dict[str, dict[int, dict]] = defaultdict(dict)
    imported_by_external = {}
    for lead in leads:
        external = lead["external_record_id"] or ""
        if external.startswith(prefix):
            imported_by_external[external] = lead
            continue
        for field in ("phone", "whatsapp"):
            number, _ = phone(lead[field])
            if number:
                prior_by_phone[number][lead["lead_id"]] = lead

    existing_rows = []
    held_rows = []
    merged_rows = []
    counts = Counter()
    held_group_index = 0
    for group in groups:
        ordered_by_row = sorted(group, key=lambda row: row["_row"])
        numbers = set().union(*(row["_phone_keys"] for row in group))
        candidates = {lead_id: lead for number in numbers for lead_id, lead in prior_by_phone.get(number, {}).items()}
        external_id = f"{prefix}{ordered_by_row[0]['_row']}"
        imported = imported_by_external.get(external_id)
        if imported:
            counts["represented_source_rows"] += len(group)
            counts["imported_leads"] += 1
            for row in ordered_by_row[1:]:
                merged_rows.append({
                    "excel_row": row["_row"], "represented_by_row": ordered_by_row[0]["_row"],
                    "crm_lead_id": imported["lead_id"], "source_name": clean(row["Contact Name"]),
                    "source_phone": clean(row["Phone"]), "source_status": clean(row["Status"]),
                    "source_counsellor": clean(row["Created By"]),
                    "effective_date": row["_created"].date().isoformat(),
                })
            continue
        if candidates:
            counts["existing_groups"] += 1
            counts["existing_rows"] += len(group)
            for row in ordered_by_row:
                sorted_candidates = [candidates[lead_id] for lead_id in sorted(candidates)]
                source_owner = OWNER_ALIASES.get(key(row["Created By"]), key(row["Created By"]))
                source_course = course_for(row["Selected Program"], catalogue, by_title)
                raw_status = clean(row["Status"])
                try:
                    source_stage = stage_fields(raw_status)["stage"]
                except ValueError:
                    source_stage = "contactable" if key(raw_status) == "texting through whatsapp discussing the details" else "unmapped"
                issue = []
                if len(sorted_candidates) > 1:
                    issue.append("Multiple CRM leads share this phone")
                else:
                    target = sorted_candidates[0]
                    if source_stage != target["stage"]:
                        issue.append("Stage differs")
                        counts["single_match_stage_differs"] += 1
                    if source_owner != key(target["counsellor_name"]):
                        issue.append("Counsellor differs")
                        counts["single_match_counsellor_differs"] += 1
                    if source_course and source_course["course_id"] != target["academic_course_id"]:
                        same_title = key(source_course["title"]) == key(target["interested_course"])
                        issue.append("Course record differs (same title)" if same_title else "Course differs")
                        counts["single_match_course_differs"] += 1
                if len(sorted_candidates) > 1:
                    counts["multiple_candidate_rows"] += 1
                existing_rows.append({
                    "excel_row": row["_row"], "source_name": clean(row["Contact Name"]),
                    "source_phone": clean(row["Phone"]), "source_mobile": clean(row["Mobile"]),
                    "source_stage": source_stage, "source_status": raw_status,
                    "source_counsellor": clean(row["Created By"]),
                    "source_programme": clean(row["Selected Program"]),
                    "source_date": row["_created"].date().isoformat(),
                    "crm_ids": ", ".join(str(target["lead_id"]) for target in sorted_candidates),
                    "crm_names": " | ".join(target["full_name"] or "" for target in sorted_candidates),
                    "crm_stages": " | ".join(target["stage"] or "" for target in sorted_candidates),
                    "crm_counsellors": " | ".join(target["counsellor_name"] or "" for target in sorted_candidates),
                    "crm_courses": " | ".join(target["interested_course"] or "" for target in sorted_candidates),
                    "crm_dates": " | ".join(str(target["created_at"].date()) if target["created_at"] else "" for target in sorted_candidates),
                    "review_flag": "; ".join(issue) or "Phone matches; no mapped stage/owner/course difference",
                    "suggested_action": "Keep existing CRM lead; inspect differences before any update",
                })
            continue
        held_group_index += 1
        group_id = f"H{held_group_index:02d}"
        owners = {OWNER_ALIASES.get(key(row["Created By"]), key(row["Created By"])) for row in group}
        if len(owners) > 1:
            reason = "Same phone, different Created By counsellors"
            suggested = "Choose one counsellor for a single lead, or confirm separate people"
        elif not any(clean(row["Contact Name"]) for row in group) and not numbers:
            reason = "No name or phone"
            suggested = "Provide a name or contact number; otherwise keep held"
        else:
            raise ValueError(f"Unclassified unimported group: {[row['_row'] for row in group]}")
        counts["held_groups"] += 1
        counts["held_rows"] += len(group)
        for row in ordered_by_row:
            held_rows.append({
                "group": group_id, "excel_row": row["_row"], "hold_reason": reason,
                "source_name": clean(row["Contact Name"]), "source_phone": clean(row["Phone"]),
                "source_mobile": clean(row["Mobile"]), "source_status": clean(row["Status"]),
                "source_counsellor": clean(row["Created By"]),
                "source_programme": clean(row["Selected Program"]),
                "effective_date": row["_created"].date().isoformat(),
                "description": clean(row["Description"]),
                "suggested_action": suggested,
            })
    expected = {"represented_source_rows": 1340, "imported_leads": 1270, "existing_groups": 496,
                "existing_rows": 524, "held_groups": 12, "held_rows": 23, "multiple_candidate_rows": 7}
    for field, value in expected.items():
        if counts[field] != value:
            raise ValueError(f"Reconciliation changed: {field}={counts[field]}, expected {value}")
    if len(merged_rows) != 70 or len(existing_rows) != 524 or len(held_rows) != 23:
        raise ValueError("Detail row counts do not reconcile")
    report = {
        "source": str(source), "source_sha256": SOURCE_SHA256,
        "counts": dict(counts), "existing_matches": existing_rows,
        "held_for_review": held_rows, "combined_into_imported_leads": merged_rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "output": str(output)}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(build(args.source, args.review, args.output))


if __name__ == "__main__":
    main()
