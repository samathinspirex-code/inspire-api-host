"""ATHE feedback form as a PDF, laid out like the ATHE sample feedback workbook.

Takes the same ``form`` dict as ``rubric_excel.build_feedback_workbook``.
"""
import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.modules.lms.rubric_excel import format_date
from app.modules.lms.rubric_grading import LEVEL_LABELS

NAVY = colors.HexColor("#17365D")
BLUE = colors.HexColor("#1F4E78")
TABLE_BLUE = colors.HexColor("#4472C4")
LABEL_FILL = colors.HexColor("#D9EAF7")
ACHIEVED_FILL = colors.HexColor("#E2F0D9")
NOT_ACHIEVED_FILL = colors.HexColor("#FCE4D6")
LINE = colors.HexColor("#BFBFBF")
GRADE_FILLS = {
    "Distinction": colors.HexColor("#C6EFCE"),
    "Merit": colors.HexColor("#DDEBF7"),
    "Pass": colors.HexColor("#E2F0D9"),
    "Resubmission": colors.HexColor("#FCE4D6"),
}

PAGE = landscape(A4)
MARGIN = 12 * mm
WIDTH = PAGE[0] - 2 * MARGIN
# Same proportions as the workbook columns: LO | LO statement | AC | P/M/D | criterion | Achieved | Evidence
COLUMN_SHARES = (22, 40, 26, 24, 58, 24, 84)
COLUMNS = [WIDTH * share / sum(COLUMN_SHARES) for share in COLUMN_SHARES]

BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=8.5, leading=11)
BOLD = ParagraphStyle("bold", parent=BASE, fontName="Helvetica-Bold")
LABEL = ParagraphStyle("label", parent=BOLD, textColor=NAVY)
HEADING = ParagraphStyle("heading", parent=BOLD, fontSize=8, leading=10, textColor=colors.white, alignment=TA_CENTER)
TITLE = ParagraphStyle("title", parent=BOLD, fontSize=15, leading=19, textColor=colors.white, alignment=TA_CENTER)
SUBTITLE = ParagraphStyle("subtitle", parent=BOLD, fontSize=11.5, leading=15, textColor=colors.white, alignment=TA_CENTER)
SECTION = ParagraphStyle("section", parent=BOLD, fontSize=9.5, leading=12, textColor=colors.white)


def _text(value, style=BASE) -> Paragraph:
    """Paragraph with user text escaped and line breaks kept."""
    body = escape("" if value is None else str(value)).replace("\n", "<br/>")
    return Paragraph(body or "&nbsp;", style)


def _banner(text, style, fill) -> Table:
    table = Table([[_text(text, style)]], colWidths=[WIDTH])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fill),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _header(form: dict) -> Table:
    grade = form.get("overall_grade") or ""
    rows = [
        ("Learner Name", form.get("learner_name"), "Learner Registration ID", form.get("learner_id")),
        ("Qualification", form.get("qualification"), "Unit Code and Title", form.get("unit_code_title")),
        ("Assignment Title", form.get("assignment_title"), "Assignment Issued", format_date(form.get("issued"))),
        ("Assignment Submitted", format_date(form.get("submitted")), "Resubmission", form.get("resubmission") or "N/A"),
        ("First Assessor", form.get("first_assessor"), "Second Assessor", form.get("second_assessor") or ""),
        ("Assessment Date", format_date(form.get("assessment_date")), "Overall Grade", grade),
    ]
    data = [[_text(a, LABEL), _text(b), _text(c, LABEL), _text(d, LABEL if c == "Overall Grade" else BASE)] for a, b, c, d in rows]
    label_width, value_width = WIDTH * 0.17, WIDTH * 0.33
    table = Table(data, colWidths=[label_width, value_width, label_width, value_width])
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("BACKGROUND", (0, 0), (0, -1), LABEL_FILL),
        ("BACKGROUND", (2, 0), (2, -1), LABEL_FILL),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    if grade in GRADE_FILLS:
        style.append(("BACKGROUND", (3, 5), (3, 5), GRADE_FILLS[grade]))
    table.setStyle(TableStyle(style))
    return table


def _criteria(form: dict) -> Table:
    headings = [
        "Learning Outcomes", "Learning Outcome Statement", "Assessment Criteria",
        "P, M, D", "Assessment Criterion Statement", "Achieved/ Not Achieved",
        "Evidence to show achievement of the LO at the standards",
    ]
    data = [[_text(heading, HEADING) for heading in headings]]
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_BLUE),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("VALIGN", (0, 1), (-1, -1), "TOP"),
    ]
    criteria = list(form.get("criteria") or [])
    group_start = 1
    for index, item in enumerate(criteria):
        row = index + 1
        new_group = index == 0 or item.get("lo_code") != criteria[index - 1].get("lo_code")
        if new_group:
            if row - 1 > group_start:
                style += [("SPAN", (0, group_start), (0, row - 1)), ("SPAN", (1, group_start), (1, row - 1))]
            group_start = row
        achieved = item.get("achieved")
        achieved_text = "Achieved" if achieved is True else "Not Achieved" if achieved is False else ""
        data.append([
            _text(item.get("lo_code") if new_group else "", LABEL),
            _text(item.get("lo_statement") if new_group else ""),
            _text(item.get("ac_code"), BOLD),
            _text(LEVEL_LABELS.get(item.get("level"), ""), BOLD),
            _text(item.get("statement")),
            _text(achieved_text, BOLD),
            _text(item.get("evidence")),
        ])
        if achieved is not None:
            style.append(("BACKGROUND", (5, row), (5, row), ACHIEVED_FILL if achieved else NOT_ACHIEVED_FILL))
    if criteria and len(criteria) > group_start:
        style += [("SPAN", (0, group_start), (0, len(criteria))), ("SPAN", (1, group_start), (1, len(criteria)))]
    # splitInRow lets a very long evidence cell continue on the next page.
    table = Table(data, colWidths=COLUMNS, repeatRows=1, splitInRow=1)
    table.setStyle(TableStyle(style))
    return table


def _section(heading: str, body, split_lines: bool) -> list:
    text = (body or "").strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()] if split_lines else [text]
    rows = []
    for line in lines or [""]:
        if split_lines and line and not line.startswith(("•", "-", "*")):
            line = f"• {line}"
        rows.append([_text(line)])
    table = Table(rows, colWidths=[WIDTH], splitInRow=1)
    table.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    # Keep the heading with at least the first lines of its text.
    return [KeepTogether([_banner(heading, SECTION, BLUE), table]), Spacer(1, 4 * mm)]


def build_feedback_pdf(form: dict) -> bytes:
    output = io.BytesIO()
    title = str(form.get("title") or "Feedback form")
    learner = str(form.get("learner_name") or "")

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#595959"))
        canvas.drawString(MARGIN, 7 * mm, f"{title}{f' - {learner}' if learner else ''}")
        canvas.drawRightString(PAGE[0] - MARGIN, 7 * mm, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        output, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=14 * mm,
        title=title, author="Inspire College", subject=f"Feedback for {learner}" if learner else "Feedback",
    )
    story = [
        _banner(form.get("title"), TITLE, NAVY),
        _banner(form.get("subtitle"), SUBTITLE, BLUE),
        Spacer(1, 4 * mm),
        _header(form),
        Spacer(1, 5 * mm),
        _criteria(form),
        Spacer(1, 5 * mm),
        *_section("Actions needed to achieve LOs and meet the standards", form.get("actions_needed"), split_lines=True),
        *_section("Overall comment and feedback", form.get("overall_comment"), split_lines=False),
    ]
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
