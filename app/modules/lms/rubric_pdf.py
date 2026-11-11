"""ATHE feedback form as a PDF.

Same content as the ATHE sample feedback workbook, styled in light blue and ash grey, with the header
(ATHE logo) and footer (wave photo strip) images from the ATHE "Assignment Feedback Form" Word template.
Takes the same ``form`` dict as ``rubric_excel.build_feedback_workbook``, plus the optional keys
``attempt_no`` and ``resubmission_due_at``.
"""
import io
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus.doctemplate import LayoutError
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.modules.lms.rubric_excel import format_date
from app.modules.lms.rubric_grading import LEVEL_LABELS

ASSETS = Path(__file__).resolve().parent / "assets"
HEADER_IMAGE = ASSETS / "feedback_header_athe.jpg"
FOOTER_IMAGE = ASSETS / "feedback_footer_wave.jpg"
COLLEGE_TIME_ZONE = "Asia/Colombo"

# Light blue + ash grey palette
INK = colors.HexColor("#1F2D3A")          # body text
SLATE = colors.HexColor("#2E4A62")        # headings and labels
BLUE = colors.HexColor("#4A7FAF")         # accent rule, title band
BLUE_SOFT = colors.HexColor("#DCE9F5")    # table headings
BLUE_MIST = colors.HexColor("#F2F7FC")    # learning outcome column
ASH = colors.HexColor("#ECEFF2")          # labels, section headings
ASH_DARK = colors.HexColor("#6B7782")     # secondary text
LINE = colors.HexColor("#C9D2DB")
ACHIEVED_FILL = colors.HexColor("#E5F2E7")
ACHIEVED_TEXT = colors.HexColor("#2F6B3C")
NOT_ACHIEVED_FILL = colors.HexColor("#F9E7E4")
NOT_ACHIEVED_TEXT = colors.HexColor("#9A3B2E")
GRADE_FILLS = {
    "Distinction": (colors.HexColor("#DFF1E3"), colors.HexColor("#256B38")),
    "Merit": (colors.HexColor("#DCE9F5"), colors.HexColor("#245A8A")),
    "Pass": (colors.HexColor("#E9EEF3"), colors.HexColor("#2E4A62")),
    "Resubmission": (colors.HexColor("#FBEBDD"), colors.HexColor("#9A4A12")),
}

PAGE = landscape(A4)
SIDE = 12 * mm
HEADER_HEIGHT = 15 * mm
FOOTER_HEIGHT = 19 * mm
WIDTH = PAGE[0] - 2 * SIDE
# Same proportions as the workbook columns: LO | LO statement | AC | P/M/D | criterion | Achieved | Evidence
COLUMN_SHARES = (22, 40, 26, 24, 58, 24, 84)
COLUMNS = [WIDTH * share / sum(COLUMN_SHARES) for share in COLUMN_SHARES]

BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK)
BOLD = ParagraphStyle("bold", parent=BASE, fontName="Helvetica-Bold")
LABEL = ParagraphStyle("label", parent=BOLD, textColor=SLATE)
HEADING = ParagraphStyle("heading", parent=BOLD, fontSize=8, leading=10, textColor=SLATE, alignment=TA_CENTER)
TITLE = ParagraphStyle("title", parent=BOLD, fontSize=14, leading=18, textColor=colors.white, alignment=TA_CENTER)
SUBTITLE = ParagraphStyle("subtitle", parent=BOLD, fontSize=10.5, leading=14, textColor=SLATE, alignment=TA_CENTER)
SECTION = ParagraphStyle("section", parent=BOLD, fontSize=9.5, leading=12, textColor=SLATE)
NOTICE = ParagraphStyle("notice", parent=BASE, fontSize=9, leading=12, textColor=colors.HexColor("#7A3E10"))


def _text(value, style=BASE) -> Paragraph:
    """Paragraph with user text escaped and line breaks kept."""
    body = escape("" if value is None else str(value)).replace("\n", "<br/>")
    return Paragraph(body or "&nbsp;", style)


def format_deadline(value) -> str:
    if not isinstance(value, datetime):
        return format_date(value)
    if value.tzinfo is not None:
        value = value.astimezone(ZoneInfo(COLLEGE_TIME_ZONE))
    return f"{value.day} {value:%B %Y}, {value:%I:%M %p}".replace(" 0", " ")


def _banner(text, style, fill, padding=5) -> Table:
    table = Table([[_text(text, style)]], colWidths=[WIDTH])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fill),
        ("TOPPADDING", (0, 0), (-1, -1), padding), ("BOTTOMPADDING", (0, 0), (-1, -1), padding + 1),
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
    fill, ink = GRADE_FILLS.get(grade, (None, SLATE))
    grade_style = ParagraphStyle("grade", parent=BOLD, fontSize=9.5, textColor=ink)
    data = [[_text(a, LABEL), _text(b), _text(c, LABEL), _text(d, grade_style if c == "Overall Grade" else BASE)] for a, b, c, d in rows]
    label_width, value_width = WIDTH * 0.17, WIDTH * 0.33
    table = Table(data, colWidths=[label_width, value_width, label_width, value_width])
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("BACKGROUND", (0, 0), (0, -1), ASH),
        ("BACKGROUND", (2, 0), (2, -1), ASH),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if fill is not None:
        style.append(("BACKGROUND", (3, 5), (3, 5), fill))
    table.setStyle(TableStyle(style))
    return table


def _resubmission_notice(form: dict) -> list:
    if form.get("overall_grade") != "Resubmission" or not form.get("resubmission_due_at"):
        return []
    message = (
        f"<b>Resubmission required.</b> Please read the feedback below, address the criteria marked Not Achieved "
        f"and hand in your resubmission in the LMS by <b>{escape(format_deadline(form['resubmission_due_at']))}</b>."
    )
    table = Table([[Paragraph(message, NOTICE)]], colWidths=[WIDTH])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FDF3EA")),
        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor("#D9822B")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("LEFTPADDING", (0, 0), (-1, -1), 9),
    ]))
    return [Spacer(1, 3 * mm), table]


def _criteria(form: dict, split_in_row: bool = False, merge_groups: bool = True) -> Table:
    headings = [
        "Learning Outcomes", "Learning Outcome Statement", "Assessment Criteria",
        "P, M, D", "Assessment Criterion Statement", "Achieved/ Not Achieved",
        "Evidence to show achievement of the LO at the standards",
    ]
    data = [[_text(heading, HEADING) for heading in headings]]
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), BLUE_SOFT),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, BLUE),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("VALIGN", (0, 1), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 1), (1, -1), BLUE_MIST),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    achieved_style = ParagraphStyle("achieved", parent=BOLD, textColor=ACHIEVED_TEXT)
    missing_style = ParagraphStyle("missing", parent=BOLD, textColor=NOT_ACHIEVED_TEXT)
    criteria = list(form.get("criteria") or [])
    group_start = 1
    for index, item in enumerate(criteria):
        row = index + 1
        new_group = index == 0 or item.get("lo_code") != criteria[index - 1].get("lo_code")
        if new_group:
            if merge_groups and row - 1 > group_start:
                style += [("SPAN", (0, group_start), (0, row - 1)), ("SPAN", (1, group_start), (1, row - 1))]
            group_start = row
            if index:
                style.append(("LINEABOVE", (0, row), (-1, row), 1, ASH_DARK))
        achieved = item.get("achieved")
        achieved_text = "Achieved" if achieved is True else "Not Achieved" if achieved is False else ""
        data.append([
            _text(item.get("lo_code") if new_group else "", LABEL),
            _text(item.get("lo_statement") if new_group else ""),
            _text(item.get("ac_code"), BOLD),
            _text(LEVEL_LABELS.get(item.get("level"), ""), BOLD),
            _text(item.get("statement")),
            _text(achieved_text, achieved_style if achieved else missing_style),
            _text(item.get("evidence")),
        ])
        if achieved is not None:
            style.append(("BACKGROUND", (5, row), (5, row), ACHIEVED_FILL if achieved else NOT_ACHIEVED_FILL))
    if merge_groups and criteria and len(criteria) > group_start:
        style += [("SPAN", (0, group_start), (0, len(criteria))), ("SPAN", (1, group_start), (1, len(criteria)))]
    # Rows move to the next page whole; splitInRow is only used when one row is taller than a page.
    table = Table(data, colWidths=COLUMNS, repeatRows=1, splitInRow=1 if split_in_row else 0)
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
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    heading_bar = Table([[_text(heading, SECTION)]], colWidths=[WIDTH])
    heading_bar.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ASH),
        ("LINEBEFORE", (0, 0), (0, -1), 3, BLUE),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    # Keep the heading with at least the first lines of its text.
    return [KeepTogether([heading_bar, table]), Spacer(1, 4 * mm)]


def _draw_image(canvas, path: Path, x: float, y: float, height: float, align_right: bool = False) -> None:
    if not path.exists():
        return
    image = ImageReader(str(path))
    width_px, height_px = image.getSize()
    width = height * width_px / height_px
    canvas.drawImage(image, x - width if align_right else x, y, width=width, height=height, mask="auto")


TOP_MARGIN = 8 * mm + HEADER_HEIGHT + 6 * mm
BOTTOM_MARGIN = FOOTER_HEIGHT + 3 * mm


def build_feedback_pdf(form: dict) -> bytes:
    # Rows normally move to the next page whole. When one row, or one learning outcome's merged group of
    # rows, is taller than a page, reportlab cannot place it that way (it raises LayoutError, or a
    # TypeError from its span handling), so build again letting rows continue overleaf.
    try:
        return _build(form, split_in_row=False)
    except (LayoutError, TypeError):
        pass
    try:
        return _build(form, split_in_row=True)
    except (LayoutError, TypeError):
        # A learning outcome group longer than a page: stop merging its cells so every row can break freely.
        return _build(form, split_in_row=True, merge_groups=False)


def _build(form: dict, split_in_row: bool, merge_groups: bool = True) -> bytes:
    output = io.BytesIO()
    title = str(form.get("title") or "Feedback form")
    learner = str(form.get("learner_name") or "")
    attempt = int(form.get("attempt_no") or 1)
    doc_label = "Assignment Feedback Form" + (f"  ·  Resubmission (attempt {attempt})" if attempt > 1 else "")
    label_style = ParagraphStyle("doclabel", parent=BOLD, fontSize=10, leading=13, textColor=SLATE, alignment=TA_RIGHT)
    sub_style = ParagraphStyle("docsub", parent=BASE, fontSize=8, leading=10, textColor=ASH_DARK, alignment=TA_RIGHT)

    def page_frame(canvas, document):
        canvas.saveState()
        top = PAGE[1] - 8 * mm
        # Header: ATHE logo on the left, document name on the right, light blue rule underneath.
        _draw_image(canvas, HEADER_IMAGE, SIDE, top - HEADER_HEIGHT, HEADER_HEIGHT)
        for paragraph, offset in ((Paragraph(escape(doc_label), label_style), 5 * mm), (Paragraph(escape(learner), sub_style), 9.5 * mm)):
            _, height = paragraph.wrap(WIDTH * 0.55, 20 * mm)
            paragraph.drawOn(canvas, PAGE[0] - SIDE - WIDTH * 0.55, top - offset - height + 3 * mm)
        canvas.setStrokeColor(BLUE)
        canvas.setLineWidth(1.2)
        canvas.line(SIDE, top - HEADER_HEIGHT - 2.5 * mm, PAGE[0] - SIDE, top - HEADER_HEIGHT - 2.5 * mm)
        # Footer: wave photo strip in the bottom-right corner, page details on the left.
        _draw_image(canvas, FOOTER_IMAGE, PAGE[0], 0, FOOTER_HEIGHT, align_right=True)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(ASH_DARK)
        canvas.drawString(SIDE, 9 * mm, f"{title}{f' - {learner}' if learner else ''}")
        canvas.drawString(SIDE, 5.5 * mm, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        output, pagesize=PAGE, leftMargin=SIDE, rightMargin=SIDE,
        topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
        title=title, author="Inspire College", subject=f"Feedback for {learner}" if learner else "Feedback",
    )
    story = [
        _banner(form.get("title"), TITLE, BLUE, padding=6),
        _banner(form.get("subtitle"), SUBTITLE, ASH),
        Spacer(1, 4 * mm),
        _header(form),
        *_resubmission_notice(form),
        Spacer(1, 5 * mm),
        _criteria(form, split_in_row, merge_groups),
        Spacer(1, 5 * mm),
        *_section("Actions needed to achieve LOs and meet the standards", form.get("actions_needed"), split_lines=True),
        *_section("Overall comment and feedback", form.get("overall_comment"), split_lines=False),
    ]
    document.build(story, onFirstPage=page_frame, onLaterPages=page_frame)
    return output.getvalue()
