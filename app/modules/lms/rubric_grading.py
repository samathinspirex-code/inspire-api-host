"""ATHE Pass / Merit / Distinction rules, matching the blank ATHE feedback-form template.

* Pass: every Pass criterion is achieved.
* Merit: every Pass and every Merit criterion is achieved ("Locked" until all Pass criteria are met).
* Distinction: every Pass, Merit and Distinction criterion is achieved ("Locked" until all Merit criteria are met).
* Otherwise "Not Yet Achieved".

A level that has no criteria in the rubric is "N/A" and does not block the next level.
The LMS-UI copy of these rules lives in LMS-UI/src/grading.js (computeRubricGrade) and must stay identical.
"""
from collections import OrderedDict

LEVELS = ("pass", "merit", "distinction")
LEVEL_LABELS = {"pass": "Pass", "merit": "Merit", "distinction": "Distinction"}
NOT_YET_ACHIEVED = "Not Yet Achieved"
OVERALL_GRADES = ("Distinction", "Merit", "Pass", NOT_YET_ACHIEVED)
GRADE_TO_BAND = {"Distinction": "distinction", "Merit": "merit", "Pass": "pass", NOT_YET_ACHIEVED: "fail"}

ACHIEVED = "Achieved"
NOT_YET = "Not yet"
LOCKED = "Locked"
NOT_APPLICABLE = "N/A"


def normalise_level(value) -> str | None:
    """Map the P / M / D column text of an ATHE rubric to a level key."""
    text = str(value or "").strip().casefold()
    if not text:
        return None
    if text.startswith("d"):
        return "distinction"
    if text.startswith("m"):
        return "merit"
    if text.startswith("p"):
        return "pass"
    return None


def level_from_ac_code(ac_code) -> str:
    """ATHE criteria such as 1M1 / 2D1 carry their level in the code; numbered ones (1.1) are Pass."""
    code = str(ac_code or "").strip().upper()
    if "D" in code[1:]:
        return "distinction"
    if "M" in code[1:]:
        return "merit"
    return "pass"


def _achieved_map(results) -> dict[str, bool | None]:
    mapping = {}
    for item in results or []:
        key = str(item.get("criterion_id"))
        achieved = item.get("achieved")
        mapping[key] = achieved if isinstance(achieved, bool) else None
    return mapping


def _level_status(criteria, achieved, unlocked: bool) -> str:
    if not criteria:
        return NOT_APPLICABLE
    if not unlocked:
        return LOCKED
    return ACHIEVED if all(achieved.get(str(item["criterion_id"])) is True for item in criteria) else NOT_YET


def compute_rubric_grade(criteria, results) -> dict:
    """Return per-LO statuses, the All Pass / Merit / Distinction rows and the overall grade.

    ``criteria`` is the assignment's criteria snapshot (dicts with criterion_id, lo_code, level).
    ``results`` is a list of ``{criterion_id, achieved}`` entries.
    """
    criteria = list(criteria or [])
    achieved = _achieved_map(results)
    by_level = {level: [item for item in criteria if item.get("level") == level] for level in LEVELS}
    anything_marked = any(achieved.get(str(item["criterion_id"])) is not None for item in criteria)
    complete = bool(criteria) and all(achieved.get(str(item["criterion_id"])) is not None for item in criteria)

    if not anything_marked:
        all_pass = all_merit = all_distinction = ""
        overall = None
    else:
        all_pass = ACHIEVED if by_level["pass"] and _level_status(by_level["pass"], achieved, True) == ACHIEVED else NOT_YET
        all_merit = _level_status(by_level["merit"], achieved, all_pass == ACHIEVED)
        merit_met = all_merit in {ACHIEVED, NOT_APPLICABLE} and all_pass == ACHIEVED
        all_distinction = _level_status(by_level["distinction"], achieved, merit_met)
        if all_distinction == ACHIEVED:
            overall = "Distinction"
        elif all_merit == ACHIEVED:
            overall = "Merit"
        elif all_pass == ACHIEVED:
            overall = "Pass"
        else:
            overall = NOT_YET_ACHIEVED

    groups: "OrderedDict[str, dict]" = OrderedDict()
    for item in criteria:
        group = groups.setdefault(item.get("lo_code") or "", {
            "lo_code": item.get("lo_code") or "",
            "lo_statement": item.get("lo_statement") or "",
            "criteria": {level: [] for level in LEVELS},
        })
        if item.get("level") in LEVELS:
            group["criteria"][item["level"]].append(item)

    group_rows = []
    for group in groups.values():
        row = {"lo_code": group["lo_code"], "lo_statement": group["lo_statement"]}
        if not anything_marked:
            row.update({level: "" for level in LEVELS})
        else:
            row["pass"] = _level_status(group["criteria"]["pass"], achieved, True)
            row["merit"] = _level_status(group["criteria"]["merit"], achieved, all_pass == ACHIEVED)
            row["distinction"] = _level_status(
                group["criteria"]["distinction"], achieved,
                all_pass == ACHIEVED and all_merit in {ACHIEVED, NOT_APPLICABLE},
            )
        group_rows.append(row)

    return {
        "groups": group_rows,
        "all_pass": all_pass,
        "all_merit": all_merit,
        "all_distinction": all_distinction,
        "overall_grade": overall,
        "complete": complete,
    }


def grade_band(overall_grade: str | None) -> str | None:
    return GRADE_TO_BAND.get(overall_grade or "")
