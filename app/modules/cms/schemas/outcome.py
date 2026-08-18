from pydantic import BaseModel, ConfigDict, Field


class OutcomeEmbedded(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    outcome_id: int
    order: int
    outcome: str


class OutcomeItem(OutcomeEmbedded):
    course_id: int


class OutcomeCreate(BaseModel):
    outcome: str = Field(..., min_length=1, max_length=255)


class OutcomeListResponse(BaseModel):
    data: list[OutcomeItem]


class OutcomeReorderRequest(BaseModel):
    outcome_ids: list[int]
