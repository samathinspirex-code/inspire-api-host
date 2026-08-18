from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.modules.cms.schemas.common import Pagination
from app.modules.cms.schemas.topic import TopicEmbedded
from app.modules.cms.schemas.outcome import OutcomeEmbedded


class ProgramCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=255)
    level: str = Field(..., min_length=1, max_length=100)
    school: str = Field(..., min_length=1, max_length=100)
    awarding_body: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=1, max_length=100)
    duration: str = Field(..., min_length=1, max_length=100)
    price_from: int
    tag: Optional[str] = Field(None, max_length=100)
    icon: str = Field(..., min_length=1, max_length=100)
    image_label: str = Field(..., min_length=1, max_length=100)
    blurb: str = Field(..., min_length=1)
    popularity: int = 0


class ProgramUpdate(ProgramCreate):
    pass


class ProgramListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    program_id: int
    slug: str
    title: str
    level: str
    school: str
    awarding_body: str
    code: str
    duration: str
    price_from: int
    tag: Optional[str]
    icon: str
    image_label: str
    blurb: str
    popularity: int


class ProgramDetail(ProgramListItem):
    model_config = ConfigDict(from_attributes=True)
    pass


class ProgramDetailWithTopicsOutcomes(ProgramDetail):
    topics: list[TopicEmbedded] = []
    outcomes: list[OutcomeEmbedded] = []


class ProgramListResponse(BaseModel):
    data: list[ProgramListItem]
    pagination: Pagination


class PublicProgramListResponse(BaseModel):
    data: list[ProgramListItem]

