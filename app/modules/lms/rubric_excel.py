"""Read ATHE rubric workbooks and write formatted ATHE feedback forms.

The import and the download both use the layout of the filled ATHE sample
("Marking Feedback" sheet): a title band, a learner / assessor header block,
an LO | LO statement | AC | P/M/D | criterion | Achieved | Evidence table,
"Actions needed" and "Overall comment and feedback".
"""
import io
import math
import re
import zipfile
from datetime import date, datetime
from zipfile import BadZipFile

from defusedxml.common import DefusedXmlException
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils.exceptions import InvalidFileException
from xml.etree.ElementTree import ParseError

from app.core.errors import ValidationError
from app.modules.lms.rubric_grading import LEVEL_LABELS, level_from_ac_code, normalise_level
from app.modules.lms.student_excel_import import MAX_EXCEL_BYTES, _check_archive

MAX_CRITERIA = 200

HEADER_LABELS = {
    "qualification": "qualification",
    "unit code and title": "unit_code_title",
    "unit code & title": "unit_code_title",
    "unit": "unit_code_title",
    "assignment title": "assignment_title",
}


# --------------------------------------------------------------------------- import

def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        if value == int(value):
            return str(int(value))
        return ("%.2f" % value).rstrip("0").rstrip(".")
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _key(value) -> str:
    return re.sub(r"\s+", " ", _text(value)).strip().casefold()


def _find_columns(row_values) -> dict[str, int] | None:
    """Map the rubric table heading row to column indexes, or None when this is not the heading row."""
    keys = [_key(value) for value in row_values]
    if not any("learning outcome" in key for key in keys) or not any("assessment criteri" in key for key in keys):
        return None
    columns: dict[str, int] = {}
    for index, key in enumerate(keys):
        if not key:
            continue
        if "learning outcome" in key and "statement" in key:
            columns.setdefault("lo_statement", index)
        elif "learning outcome" in key:
            columns.setdefault("lo_code", index)
        elif "criteri" in key and "statement" in key:
            columns.setdefault("statement", index)
        elif "assessment criteri" in key:
            columns.setdefault("ac_code", index)
        elif key.startswith("p, m, d") or key.startswith("p,m,d") or key.startswith("p/m/d") or key.startswith("grade"):
            columns.setdefault("level", index)
        elif "achieved" in key:
            columns.setdefault("achieved", index)
        elif "evidence" in key:
            columns.setdefault("evidence", index)
    # Positional fallback for the standard 7-column ATHE layout.
    for name, index in (("lo_code", 0), ("lo_statement", 1), ("ac_code", 2), ("level", 3), ("statement", 4)):
        columns.setdefault(name, index)
    return columns


def parse_rubric_workbook(data: bytes, file_name: str | None = None) -> dict:
    """Return a rubric template draft (header fields + criteria) from an ATHE rubric workbook.

    Learner-specific values (learner name, achieved, evidence, comments) are ignored.
    """
    workbook = None
    try:
        if not data or len(data) > MAX_EXCEL_BYTES:
            raise ValidationError("Rubric files must be .xlsx and at most 2 MB.")
        _check_archive(data, table_label="rubric")
        workbook = load_workbook(io.BytesIO(data), read_only=False, data_only=True, keep_links=False, keep_vba=False)
        sheets = [sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"]
        if not sheets:
            raise ValidationError("The workbook has no visible worksheets.")
        sheet = sheets[0]
        if sheet.max_row > 2000 or sheet.max_column > 30:
            raise ValidationError("The rubric sheet is too large. Keep the rubric on one sheet with at most 30 columns.")

        rows = [[cell.value for cell in row] for row in sheet.iter_rows()]
        heading_index = None
        columns = None
        for index, values in enumerate(rows):
            columns = _find_columns(values)
            if columns:
                heading_index = index
                break
        if heading_index is None or columns is None:
            raise ValidationError(
                "Could not find the rubric table. The sheet needs a heading row with "
                "'Learning Outcomes' and 'Assessment Criteria' columns, like the ATHE sample rubric."
            )

        header = {"qualification": "", "unit_code_title": "", "assignment_title": ""}
        title_lines = []
        for values in rows[:heading_index]:
            texts = [_text(value) for value in values]
            filled = [text for text in texts if text]
            if len(filled) == 1 and not header["qualification"] and len(title_lines) < 2:
                title_lines.append(filled[0])
                continue
            for position, text in enumerate(texts):
                field = HEADER_LABELS.get(_key(text))
                if not field or header[field]:
                    continue
                following = next((value for value in texts[position + 1:] if value), "")
                if following and _key(following) not in HEADER_LABELS:
                    header[field] = following

        def cell(values, name):
            position = columns.get(name)
            return values[position] if position is not None and position < len(values) else None

        criteria = []
        current_lo, current_lo_statement = "", ""
        for values in rows[heading_index + 1:]:
            first = _key(values[0] if values else "")
            if first.startswith("actions needed") or first.startswith("overall comment") or first.startswith("overall feedback"):
                break
            lo_code = _text(cell(values, "lo_code"))
            lo_statement = _text(cell(values, "lo_statement"))
            if lo_code:
                current_lo, current_lo_statement = lo_code, lo_statement or ""
            elif lo_statement:
                current_lo_statement = lo_statement
            ac_code = _text(cell(values, "ac_code"))
            statement = _text(cell(values, "statement"))
            if not ac_code and not statement:
                continue
            if not ac_code:
                raise ValidationError(f"Row {heading_index + 2 + len(criteria)}: an assessment criterion code (e.g. 1.1 or 1M1) is missing.")
            level = normalise_level(cell(values, "level")) or level_from_ac_code(ac_code)
            criteria.append({
                "lo_code": (current_lo or ac_code.split(".")[0])[:30],
                "lo_statement": current_lo_statement,
                "ac_code": ac_code[:30],
                "level": level,
                "statement": statement or ac_code,
            })
            if len(criteria) > MAX_CRITERIA:
                raise ValidationError(f"A rubric can have at most {MAX_CRITERIA} assessment criteria.")
        if not criteria:
            raise ValidationError("The rubric table has no assessment criteria rows.")
        if not any(item["level"] == "pass" for item in criteria):
            raise ValidationError("The rubric needs at least one Pass criterion.")

        base_name = re.sub(r"\.xlsx$", "", (file_name or "").strip(), flags=re.IGNORECASE).strip()
        qualification = header["qualification"] or (title_lines[0] if title_lines else "")
        return {
            "name": (base_name or title_lines[-1] if (base_name or title_lines) else "Imported rubric")[:255],
            "form_title": (title_lines[1] if len(title_lines) > 1 else "")[:255],
            "qualification": qualification[:255],
            "unit_code_title": header["unit_code_title"][:255],
            "default_assignment_title": header["assignment_title"][:255],
            "source_file_name": (file_name or "")[:255] or None,
            "criteria": criteria,
        }
    except ValidationError:
        raise
    except (BadZipFile, InvalidFileException, ParseError, DefusedXmlException, ValueError, KeyError, TypeError, OSError, RuntimeError, OverflowError):
        raise ValidationError("Could not read this Excel file. Use an unprotected .xlsx workbook; .xls and .xlsm are not supported.") from None
    finally:
        if workbook is not None:
            workbook.close()


# --------------------------------------------------------------------------- export

NAVY = "FF17365D"
BLUE = "FF1F4E78"
TABLE_BLUE = "FF4472C4"
LABEL_FILL = "FFD9EAF7"
ACHIEVED_FILL = "FFE2F0D9"
NOT_ACHIEVED_FILL = "FFFCE4D6"
GRADE_FILLS = {
    "Distinction": "FFC6EFCE",
    "Merit": "FFDDEBF7",
    "Pass": "FFE2F0D9",
    "Resubmission": "FFFCE4D6",
}
COLUMN_WIDTHS = {"A": 29.86, "B": 36.0, "C": 18.0, "D": 23.57, "E": 54.0, "F": 18.0, "G": 88.0}
THIN = Side(style="thin", color="FFBFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def format_date(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    if isinstance(value, datetime):
        value = value.date()
    return f"{value.day} {value:%B %Y}"


def _safe(value) -> str:
    """Never write text Excel would treat as a formula."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) and not text.startswith("- ") else text


def _row_height(text: str, width_chars: float, minimum: float = 24.0) -> float:
    lines = 0
    for part in (text or "").split("\n"):
        lines += max(1, math.ceil(len(part) / max(width_chars, 1)))
    return max(minimum, min(409.0, 15.0 * lines + 6))


def build_feedback_workbook(form: dict) -> bytes:
    """Build the ATHE feedback form.

    ``form`` keys: title, subtitle, learner_name, learner_id, qualification, unit_code_title,
    assignment_title, issued, submitted, resubmission, first_assessor, second_assessor,
    assessment_date, overall_grade, criteria (list of dicts with lo_code, lo_statement, ac_code,
    level, statement, achieved, evidence), actions_needed, overall_comment.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Marking Feedback"
    sheet.sheet_view.showGridLines = False
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    for column, width in COLUMN_WIDTHS.items():
        sheet.column_dimensions[column].width = width

    white_bold = Font(name="Calibri", bold=True, color="FFFFFFFF")
    wrap_top = Alignment(vertical="top", wrap_text=True)

    sheet.merge_cells("A1:G1")
    sheet["A1"] = _safe(form.get("title"))
    sheet["A1"].font = Font(name="Calibri", size=15, bold=True, color="FFFFFFFF")
    sheet["A1"].fill = PatternFill("solid", fgColor=NAVY)
    sheet["A1"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 27.95
    sheet.merge_cells("A2:G2")
    sheet["A2"] = _safe(form.get("subtitle"))
    sheet["A2"].font = Font(name="Calibri", size=12, bold=True, color="FFFFFFFF")
    sheet["A2"].fill = PatternFill("solid", fgColor=BLUE)
    sheet["A2"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[2].height = 24

    header_rows = [
        ("Learner Name", form.get("learner_name"), "Learner Registration ID", form.get("learner_id")),
        ("Qualification", form.get("qualification"), "Unit Code and Title", form.get("unit_code_title")),
        ("Assignment Title", form.get("assignment_title"), "Assignment Issued", format_date(form.get("issued"))),
        ("Assignment Submitted", format_date(form.get("submitted")), "Resubmission", form.get("resubmission") or "N/A"),
        ("First Assessor", form.get("first_assessor"), "Second Assessor", form.get("second_assessor") or ""),
        ("Assessment Date", format_date(form.get("assessment_date")), "Overall Grade", form.get("overall_grade") or ""),
    ]
    label_font = Font(name="Calibri", bold=True, color=NAVY)
    label_fill = PatternFill("solid", fgColor=LABEL_FILL)
    for offset, (left_label, left_value, right_label, right_value) in enumerate(header_rows):
        row = 4 + offset
        sheet.merge_cells(f"B{row}:C{row}")
        sheet.merge_cells(f"F{row}:G{row}")
        for column, value, is_label in (("A", left_label, True), ("B", left_value, False), ("E", right_label, True), ("F", right_value, False)):
            target = sheet[f"{column}{row}"]
            target.value = _safe(value)
            target.alignment = wrap_top
            if is_label:
                target.font = label_font
                target.fill = label_fill
        for column in "ABCEFG":
            sheet[f"{column}{row}"].border = BOX
    grade_cell = sheet["F9"]
    grade_cell.font = Font(name="Calibri", bold=True, color=NAVY)
    if form.get("overall_grade") in GRADE_FILLS:
        grade_cell.fill = PatternFill("solid", fgColor=GRADE_FILLS[form["overall_grade"]])

    headings = [
        "Learning Outcomes", "Learning Outcome Statement ", "Assessment Criteria",
        "P, M, D\n(specify grade as appropriate)", "Assessment Criterion Statement",
        "Achieved/ Not Achieved", "Evidence to show achievement of the LO at the standards",
    ]
    for index, heading in enumerate(headings):
        target = sheet.cell(row=11, column=index + 1, value=heading)
        target.font = white_bold
        target.fill = PatternFill("solid", fgColor=TABLE_BLUE)
        target.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        target.border = BOX
    sheet.row_dimensions[11].height = 42

    row = 12
    criteria = list(form.get("criteria") or [])
    group_start = row
    for index, item in enumerate(criteria):
        new_group = index == 0 or item.get("lo_code") != criteria[index - 1].get("lo_code")
        if new_group:
            if index:
                _merge_group(sheet, group_start, row - 1)
            group_start = row
            sheet.cell(row=row, column=1, value=_safe(item.get("lo_code")))
            sheet.cell(row=row, column=2, value=_safe(item.get("lo_statement")))
        achieved = item.get("achieved")
        achieved_text = "Achieved" if achieved is True else "Not Achieved" if achieved is False else ""
        values = [None, None, item.get("ac_code"), LEVEL_LABELS.get(item.get("level"), ""), item.get("statement"), achieved_text, item.get("evidence")]
        for column, value in enumerate(values, start=1):
            target = sheet.cell(row=row, column=column)
            if value is not None:
                target.value = _safe(value)
            target.alignment = Alignment(horizontal="left" if column <= 2 else None, vertical="top", wrap_text=True)
            target.border = BOX
        sheet.cell(row=row, column=1).font = Font(name="Calibri", bold=True, color=NAVY)
        sheet.cell(row=row, column=3).font = Font(name="Calibri", bold=True)
        sheet.cell(row=row, column=4).font = Font(name="Calibri", bold=True)
        if achieved is not None:
            sheet.cell(row=row, column=6).fill = PatternFill("solid", fgColor=ACHIEVED_FILL if achieved else NOT_ACHIEVED_FILL)
        sheet.row_dimensions[row].height = max(72.0, _row_height(item.get("evidence") or "", 95, 72.0), _row_height(item.get("statement") or "", 55, 72.0))
        row += 1
    if criteria:
        _merge_group(sheet, group_start, row - 1)

    row += 1
    row = _section(sheet, row, "Actions needed to achieve LOs and meet the standards", form.get("actions_needed"), split_lines=True)
    row = _section(sheet, row, "Overall comment and feedback", form.get("overall_comment"), split_lines=False)

    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _merge_group(sheet, start: int, end: int) -> None:
    if end > start:
        sheet.merge_cells(start_row=start, start_column=1, end_row=end, end_column=1)
        sheet.merge_cells(start_row=start, start_column=2, end_row=end, end_column=2)


def _section(sheet, row: int, heading: str, body, split_lines: bool) -> int:
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
    target = sheet.cell(row=row, column=1, value=heading)
    target.font = Font(name="Calibri", bold=True, color="FFFFFFFF")
    target.fill = PatternFill("solid", fgColor=BLUE)
    target.alignment = Alignment(vertical="center")
    sheet.row_dimensions[row].height = 24
    row += 1
    text = (body or "").strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()] if split_lines else [text]
    for line in lines or [""]:
        if split_lines and line and not line.startswith(("•", "-", "*")):
            line = f"• {line}"
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        target = sheet.cell(row=row, column=1, value=_safe(line))
        target.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        sheet.row_dimensions[row].height = _row_height(line, 260, 30.0)
        row += 1
    return row + 1


def feedback_file_name(rubric_title: str | None, learner_name: str | None, suffix: str | None = None, extension: str = ".xlsx") -> str:
    """Download name: "<rubric title> - <student name>[ - suffix].xlsx", safe for every operating system."""
    parts = [str(part).strip() for part in (rubric_title, learner_name, suffix) if part and str(part).strip()]
    name = " - ".join(parts) or "Feedback"
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return f"{name[:150].rstrip(' .-') or 'Feedback'}{extension}"


def build_feedback_zip(files: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    used = set()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files:
            stem, dot, extension = name.rpartition(".")
            candidate, counter = name, 2
            while candidate in used:
                candidate = f"{stem} ({counter}).{extension}" if dot else f"{name} ({counter})"
                counter += 1
            used.add(candidate)
            archive.writestr(candidate, content)
    return output.getvalue()
