"""Bounded, admin-only CSV import. Preview never writes; creation is atomic."""
import csv
import io
from collections import Counter

from pydantic import ValidationError as ModelValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationError
from app.modules.lms.repository.people import PeopleRepository
from app.modules.lms.schemas.people import StudentCreate
from app.modules.lms.schemas.student_import import StudentImportRequest, StudentImportResponse, StudentImportRow

MAX_ROWS = 100
REQUIRED = {"full_name", "email", "student_number"}
OPTIONAL = {"phone", "notes"}


def parse_students(text: str) -> list[StudentImportRow]:
    if len(text) > 500_000 or "\x00" in text:
        raise ValidationError("CSV must be plain UTF-8 text, at most 500,000 characters.")
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff"), newline=""), strict=True)
    rows = []
    try:
        header = next(reader, [])
        header = [value.strip().lower() for value in header]
        if len(header) != len(set(header)):
            raise ValidationError("CSV has duplicate column headers.")
        if not REQUIRED.issubset(header) or set(header) - REQUIRED - OPTIONAL:
            raise ValidationError("Use columns full_name, email, student_number, phone, notes. The first three are required.")
        for cells in reader:
            if not any(value.strip() for value in cells):
                continue
            if len(rows) >= MAX_ROWS:
                raise ValidationError("Import a maximum of 100 students at a time.")
            row = StudentImportRow(row=reader.line_num)
            rows.append(row)
            if len(cells) != len(header):
                row.errors.append("Column count does not match the header. Put values containing commas in double quotes.")
                continue
            values = {key: value.strip() for key, value in zip(header, cells)}
            values["email"] = values["email"].lower()
            values["student_number"] = values["student_number"].upper()
            for key in OPTIONAL:
                values[key] = values.get(key) or None
            for key, value in values.items():
                setattr(row, key, value)
            try:
                validated = StudentCreate.model_validate(values)
                row.email = str(validated.email).lower()
                if len(row.email) > 255:
                    row.errors.append("email: Must be at most 255 characters.")
            except ModelValidationError as exc:
                row.errors.extend(f"{error['loc'][0]}: {error['msg']}" for error in exc.errors())
    except csv.Error:
        raise ValidationError("CSV could not be read. Check quoted values and save as CSV UTF-8.") from None
    if not rows:
        raise ValidationError("Add at least one student below the column headers.")
    for field, label in [("email", "Email"), ("student_number", "Student number")]:
        counts = Counter(getattr(row, field) for row in rows if getattr(row, field))
        for row in rows:
            if counts[getattr(row, field)] > 1:
                row.errors.append(f"{label} is repeated in this CSV.")
    return rows


async def import_students(db: AsyncSession, payload: StudentImportRequest, created_by: int) -> StudentImportResponse:
    rows = parse_students(payload.csv_text)
    return await import_student_rows(db, rows, payload.preview, created_by)


async def import_student_rows(db: AsyncSession, rows: list[StudentImportRow], preview: bool, created_by: int) -> StudentImportResponse:
    repo = PeopleRepository(db)
    existing_emails, existing_numbers = await repo.student_import_conflicts(
        [row.email for row in rows if row.email], [row.student_number for row in rows if row.student_number]
    )
    for row in rows:
        if row.email in existing_emails:
            row.errors.append("Email already belongs to an account; existing accounts will not be changed.")
        if row.student_number.lower() in existing_numbers:
            row.errors.append("Student number already exists.")
    access = await repo.access_levels(["LMS", "STUDENT"])
    if {item.access_key for item in access} != {"LMS", "STUDENT"}:
        raise ValidationError("LMS and STUDENT access levels must be seeded before importing students.")
    result = StudentImportResponse(rows=rows, can_import=not any(row.errors for row in rows))
    if preview or not result.can_import:
        return result
    # Revalidated above on every confirmation. No emails run inside this transaction.
    try:
        ids = await repo.create_students_bulk(rows, access, created_by)
    except IntegrityError:
        await db.rollback()
        raise ConflictError("An email or student number was added during import. No students from this batch were created; preview again.") from None
    for row, user_id in zip(rows, ids):
        row.user_id = user_id
    result.imported = len(rows)
    result.can_import = False
    return result
