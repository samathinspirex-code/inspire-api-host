"""Import the counsellors' November lead sheets into the CRM.

Reads the two hand-kept workbooks (layouts below), maps short course names to the
catalogue, and either updates the existing lead (matched by the last 9 digits of
phone or WhatsApp) or creates a new one. Existing leads take the sheet's counsellor
and course; their stage only moves forward, never back. Each sheet remark is kept
as a note. Re-running changes nothing that is already imported.

* ATHE_JAIN Students Leads.xlsx, sheet "November": main block A-H (Date, Number,
  Name, Counsellor, Programme, Source, Stage, Update), JAIN block M-R, chat-bot
  block U-Z. A date is written only on the first row of a day and carries down.
* cpd leads .xlsx: one sheet per counsellor with side-by-side blocks; a header row
  `date | contact | | name | remarks | <course>` starts a block and the next header
  in the same columns ends it.

    python scripts/import_counsellor_sheets.py ATHE.xlsx CPD.xlsx            preview only
    python scripts/import_counsellor_sheets.py ATHE.xlsx CPD.xlsx --apply    write
"""

import argparse
import asyncio
import re
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.database import engine  # noqa: E402
from merge_duplicate_crm_leads import RANK  # noqa: E402

SL = timezone(timedelta(hours=5, minutes=30))
COURSES = {  # normalized sheet text -> academic_courses.course_id
    "hnd in ai": 7, "hnd ai": 7, "hnd in bm": 19, "hnd bm": 19, "health care hnd": 37, "hnd health": 37,
    "hnd in health": 37, "hnd in cyber": 6, "hnd in data analytics": 5, "hnd in data analyrics": 5,
    "hnd in accounting": 20, "fo law": 34, "fou law": 34, "law": 34, "fo health": 36, "fou health": 36,
    "foundation in health": 36, "fou eng comm": 16, "foundation in english commiuni": 16,
    "fou small business": 18, "foundation in small business": 18, "fou in small business": 18,
    "foundation in accounting": 15, "fou in acc": 15, "fou app stat": 17, "fo app stat": 17,
    "foundation in applied stat": 17, "foundation in business": 14, "pg acc fin": 27, "finance pg": 27,
    "pg strategic educational leadership and management": 45, "pg ai": 8, "ai pg": 8, "pg health": 41,
    "b com": 73,
    # CPD block titles
    "ai for hr": 48, "ai mastery": 49, "dmm": 89, "digital marketing mastery": 89, "digital marketing": 89,
    "fl": 88, "finance literacy": 88, "ai for marketing": 47, "data analytics": 50,
    "cyber security with smart era": 96,
}
FREE_FOUNDATION = {"free f", "free foundation"}
CAMPAIGNS = {"shenoli video": "Shenoli video"}
# In these sheets "WhatsApp" means a Meta ad that opened WhatsApp (your rule, 16 Nov 2026); "Chat Bot" is the bot.
SOURCES = {"whatsapp": "meta", "chat bot": "whatsapp", "lead form": "meta_lead_forms", "direct calls": "hotline_call"}
COUNSELLORS = {"shaniya": 7, "samindi": 4, "thiruni": 8, "jennifer": 5, "eugine": 25}
LABELS = {"new_lead": "New Lead", "uncontactable": "Uncontactable", "contacted": "Contacted", "follow_up": "Follow Up",
          "not_interested": "Not Interested"}


def words(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def digits_of(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\D", "", str(value or ""))


def key9(value) -> str | None:
    d = digits_of(value)
    return d[-9:] if len(d) >= 9 else None


def display_phone(value) -> str:
    d = digits_of(value)
    if len(d) == 9 and d.startswith("7"):
        return f"+94{d}"
    if len(d) == 10 and d.startswith("0"):
        return f"+94{d[1:]}"
    return f"+{d}"


def stage_of(raw_stage, remark) -> tuple[str, bool]:
    """(stage, inferred). A blank stage is read from the remark."""
    first = words(raw_stage).split(" ")[0] if words(raw_stage) else ""
    if first.startswith("unco") or first.startswith("uncn"):
        return "uncontactable", False
    if first in {"contacted", "contactable"}:
        return "contacted", False
    if words(raw_stage).startswith("not interested"):
        return "not_interested", False
    if first == "followup" or words(raw_stage).startswith("follow up"):
        return "follow_up", False
    note = words(remark)
    if not note:
        return "new_lead", True
    if re.search(r"didn t inquire|didnt inquire|not inquired|not interested|unqualif|fees beyond|junk|wrong num", note):
        return "not_interested", True
    if re.search(r"^(uncontactable|no answer|no response|line busy|declined|unavailable|voice ?mail|phone switched)", note):
        return "uncontactable", True
    if re.search(r"contacted|asked|discussing|call back|get back|interested|mentioned|son has|details", note):
        return "contacted", True
    return "new_lead", True


def read_athe(path: Path) -> list[dict]:
    sheet = load_workbook(path, read_only=True, data_only=True)["November"]
    rows, day = [], None
    for number, raw in enumerate(sheet.iter_rows(min_row=6, values_only=True), start=6):
        r = list(raw) + [None] * 30
        if isinstance(r[0], datetime):
            day = r[0]
        if r[1] not in (None, ""):
            rows.append({"ref": f"athe-november-r{number}cB", "phone": r[1], "name": r[2], "owner": r[3], "programme": r[4],
                         "source": r[5], "stage": r[6], "remark": r[7], "day": day})
        if r[13] not in (None, ""):
            rows.append({"ref": f"athe-november-r{number}cN", "phone": r[13], "name": None, "owner": r[14], "programme": r[15],
                         "source": r[16], "stage": None, "remark": r[17], "day": r[12] if isinstance(r[12], datetime) else day})
        if r[21] not in (None, ""):
            rows.append({"ref": f"athe-november-r{number}cV", "phone": r[21], "name": None, "owner": r[22], "programme": r[23],
                         "source": r[24], "stage": None, "remark": r[25], "day": r[20] if isinstance(r[20], datetime) else day})
    return rows


def read_cpd(path: Path) -> list[dict]:
    rows = []
    for sheet in load_workbook(path, read_only=True, data_only=True).worksheets:
        grid = [list(r) + [None] * 30 for r in sheet.iter_rows(values_only=True)]
        owner = sheet.title.strip()
        for i, header in enumerate(grid):
            for j, cell in enumerate(header):
                if not (isinstance(cell, str) and cell.strip().lower() == "contact"):
                    continue
                labels = [x.strip() for x in header[j + 1:j + 5] if isinstance(x, str) and x.strip()]
                course = next((x for x in labels if words(x) not in {"name"} and not words(x).startswith("remarks")), None)
                if course is None:  # "remarks  AI mastery"
                    course = next((x[7:].strip() for x in labels if words(x).startswith("remarks") and len(x) > 8), None)
                day = header[j - 1] if j and isinstance(header[j - 1], datetime) else None
                for n, r in enumerate(grid[i + 1:], start=i + 2):
                    value = r[j]
                    if isinstance(value, str) and value.strip().lower() == "contact":
                        break
                    if j and isinstance(r[j - 1], datetime):
                        day = r[j - 1]
                    if value in (None, "") or not key9(value):
                        continue
                    rows.append({"ref": f"cpd-{words(owner)}-r{n}c{j + 1}", "phone": value, "name": r[j + 2], "owner": owner,
                                 "programme": course, "source": None, "stage": r[j + 1], "remark": r[j + 3], "day": day})
    return rows


def combine(rows: list[dict]) -> list[dict]:
    """One row per number: the latest day wins, remarks are joined."""
    by_number: dict[str, dict] = {}
    for row in rows:
        k = key9(row["phone"])
        if not k:
            continue
        row["key"] = k
        current = by_number.get(k)
        if current is None:
            by_number[k] = {**row, "remarks": [row]}
            continue
        current["remarks"].append(row)
        if (row["day"] or datetime.min) >= (current["day"] or datetime.min):
            by_number[k] = {**row, "remarks": current["remarks"]}
    return list(by_number.values())


def remark_text(row: dict) -> str:
    parts = []
    for item in row["remarks"]:
        remark = str(item.get("remark") or "").strip()
        stage = str(item.get("stage") or "").strip()
        when = item["day"].strftime("%d %b") if item.get("day") else "Nov"
        bits = [b for b in (stage, remark) if b]
        parts.append(f"{when}: {' - '.join(bits) if bits else '(no remark)'}")
    return "Counsellor sheet " + "; ".join(parts)


async def main(athe: Path, cpd: Path, apply: bool) -> None:
    engine.echo = False
    rows = combine(read_athe(athe) + read_cpd(cpd))
    async with engine.connect() as conn:
        catalogue = {r[0]: (r[1], r[2]) for r in (await conn.execute(text(
            "SELECT course_id, title, awarding_body FROM academic_courses"))).all()}
        names = {r[0]: r[1] for r in (await conn.execute(text("SELECT user_id, full_name FROM users"))).all()}
        leads = (await conn.execute(text(
            "SELECT lead_id, phone, whatsapp, stage, assigned_counsellor_id, academic_course_id, external_record_id, free_foundation "
            "FROM crm_leads WHERE is_archived = false ORDER BY lead_id DESC"))).mappings().all()
        notes = {(r[0], r[1]) for r in (await conn.execute(text(
            "SELECT lead_id, content FROM crm_activities WHERE content LIKE 'Counsellor sheet %'"))).all()}
    by_key = {}
    for lead in leads:
        for k in (key9(lead["phone"]), key9(lead["whatsapp"])):
            if k:
                by_key.setdefault(k, lead)

    counts, unmapped, inferred, per_owner, plan = Counter(), Counter(), Counter(), Counter(), []
    tomorrow_noon = datetime.combine(datetime.now(SL).date() + timedelta(days=1), time(12), SL).astimezone(timezone.utc).replace(tzinfo=None)
    for row in rows:
        prog = words(row["programme"])
        course_id = COURSES.get(prog)
        if prog and course_id is None and prog not in FREE_FOUNDATION and prog not in CAMPAIGNS:
            unmapped[str(row["programme"]).strip()] += 1
        stage, guessed = stage_of(row["stage"], row["remark"])
        if guessed:
            inferred[LABELS[stage]] += 1
        owner_id = COUNSELLORS.get(words(row["owner"]).split(" ")[0] if words(row["owner"]) else "")
        if row["owner"] and owner_id is None:
            counts[f"unknown counsellor: {row['owner']}"] += 1
        per_owner[names.get(owner_id, "(none)")] += 1
        values = {"course_id": course_id, "stage": stage, "owner_id": owner_id,
                  "free_foundation": prog in FREE_FOUNDATION, "campaign": CAMPAIGNS.get(prog),
                  "programme_text": None if course_id or prog in FREE_FOUNDATION or prog in CAMPAIGNS else (str(row["programme"]).strip() or None)}
        note = remark_text(row)
        lead = by_key.get(row["key"])
        if lead is None:
            counts["new"] += 1
            plan.append(("new", row, values, note, None, {}))
            continue
        counts["existing"] += 1
        change = {}
        if owner_id and lead["assigned_counsellor_id"] != owner_id:
            change["assigned_counsellor_id"] = owner_id
        if course_id and lead["academic_course_id"] != course_id:
            change["academic_course_id"] = course_id
        if RANK.get(stage, 0) > RANK.get(lead["stage"], 0):
            change["stage"] = stage
            counts["existing: stage moved forward"] += 1
        elif stage != lead["stage"]:
            counts["existing: stage kept (already further along)"] += 1
        if values["free_foundation"] and not lead["free_foundation"]:
            change["free_foundation"] = True
        add_note = (lead["lead_id"], note) not in notes
        if change or add_note:
            plan.append(("update", row, values, note if add_note else None, lead, change))
            counts["existing: to update"] += 1
    print(f"Rows: {len(rows)} unique numbers")
    for label, n in sorted(counts.items()):
        print(f"  {label}: {n}")
    print("  courses:", Counter(catalogue[v["course_id"]][0] if v["course_id"] else "(no course)" for _, _, v, _, _, _ in plan).most_common())
    print("  unmapped programme text (kept as text):", unmapped.most_common())
    print("  blank stage read from remark:", inferred.most_common())
    print("  per counsellor:", per_owner.most_common())
    print("  new-lead stages:", Counter(LABELS[v["stage"]] for k, _, v, _, _, _ in plan if k == "new").most_common())
    if not apply:
        print("Preview only; add --apply to write.")
        await engine.dispose()
        return

    async with engine.begin() as conn:
        for kind, row, values, note, lead, change in plan:
            course = catalogue.get(values["course_id"]) if values["course_id"] else None
            if kind == "new":
                source = SOURCES.get(words(row["source"]), "manual")
                day = (row["day"] or datetime.now()).date()
                lead_id = (await conn.execute(text(
                    "INSERT INTO crm_leads (full_name, phone, whatsapp, source, stage, priority, academic_course_id, "
                    "interested_course, awarding_body, interested_programme, campaign, free_foundation, followup_date, "
                    "assigned_counsellor_id, counsellor_name, assigned_at, external_record_id, created_at, updated_at) "
                    "VALUES (:name, :phone, :phone, :source, :stage, 'medium', :course_id, :course_title, :body, :programme, "
                    ":campaign, :free, :followup, :owner, :owner_name, :assigned_at, :ref, :created, now()) RETURNING lead_id"),
                    {"name": (str(row["name"]).strip() if row["name"] else None) or display_phone(row["phone"]),
                     "phone": display_phone(row["phone"]), "source": source, "stage": values["stage"],
                     "course_id": values["course_id"], "course_title": course[0] if course else None,
                     "body": course[1] if course else None, "programme": values["programme_text"],
                     "campaign": values["campaign"], "free": values["free_foundation"],
                     "followup": tomorrow_noon if values["stage"] == "follow_up" else None,
                     "owner": values["owner_id"], "owner_name": names.get(values["owner_id"]),
                     "assigned_at": datetime.combine(day, time(9)) if values["owner_id"] else None,
                     "ref": f"sheet-{row['ref']}", "created": datetime.combine(day, time(9))})).scalar_one()
            else:
                lead_id = lead["lead_id"]
                sets = dict(change)
                if "assigned_counsellor_id" in sets:
                    sets["counsellor_name"] = names.get(sets["assigned_counsellor_id"])
                    sets["assigned_at"] = datetime.now()
                if "academic_course_id" in sets and course:
                    sets["interested_course"], sets["awarding_body"] = course
                if sets.get("stage") == "follow_up":
                    sets["followup_date"] = tomorrow_noon
                if sets:
                    assignments = ", ".join(f"{k} = :{k}" for k in sets)
                    await conn.execute(text(f"UPDATE crm_leads SET {assignments}, updated_at = now() WHERE lead_id = :lead_id"),
                                       {**sets, "lead_id": lead_id})
            if note:
                await conn.execute(text("INSERT INTO crm_activities (lead_id, activity_type, content) VALUES (:id, 'note', :c)"),
                                   {"id": lead_id, "c": note})
    print(f"Applied: {sum(1 for p in plan if p[0] == 'new')} created, {sum(1 for p in plan if p[0] == 'update')} updated.")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("athe", type=Path)
    parser.add_argument("cpd", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.athe, args.cpd, args.apply))
