from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DashboardProgram(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    program_id: int
    title: str
    school: str
    level: str
    awarding_body: str


class CmsDashboardResponse(BaseModel):
    total_programs: int = Field(ge=0)
    total_students: int = Field(ge=0)
    published_content: int = Field(ge=0)
    draft_content: int = Field(ge=0)
    published_by_type: dict[str, int]
    recent_programs: list[DashboardProgram]
    generated_at: datetime
