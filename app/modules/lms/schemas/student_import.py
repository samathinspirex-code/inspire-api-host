from pydantic import BaseModel, Field


class StudentImportRequest(BaseModel):
    csv_text: str = Field(..., min_length=1, max_length=500_000)
    preview: bool = True


class StudentImportRow(BaseModel):
    row: int
    full_name: str = ""
    email: str = ""
    student_number: str = ""
    phone: str | None = None
    notes: str | None = None
    errors: list[str] = Field(default_factory=list)
    user_id: int | None = None


class StudentImportResponse(BaseModel):
    rows: list[StudentImportRow]
    can_import: bool
    imported: int = 0
    sheet_name: str | None = None
