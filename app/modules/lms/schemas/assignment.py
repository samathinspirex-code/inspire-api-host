from datetime import datetime

from pydantic import BaseModel, Field


class AssignPersonRequest(BaseModel):
    user_id: int = Field(..., gt=0)


class AssignmentPersonItem(BaseModel):
    user_id: int
    full_name: str
    email: str
    reference_number: str
    secondary_label: str | None
    profile_image_url: str | None = None
    status: str
    assigned_at: datetime


class AssignmentListResponse(BaseModel):
    data: list[AssignmentPersonItem]
    capacity: int | None = None
    assigned_count: int
