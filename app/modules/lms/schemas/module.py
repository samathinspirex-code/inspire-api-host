from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ModuleStatus = Literal["draft", "active"]


class ModuleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    status: ModuleStatus = "draft"


class ModuleUpdate(ModuleCreate):
    pass


class ModuleItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    module_id: int
    course_id: int
    title: str
    description: str | None
    position: int
    status: ModuleStatus
    created_at: datetime
    updated_at: datetime


class ModuleListResponse(BaseModel):
    data: list[ModuleItem]


class ModuleReorderRequest(BaseModel):
    module_ids: list[int] = Field(..., min_length=1)
