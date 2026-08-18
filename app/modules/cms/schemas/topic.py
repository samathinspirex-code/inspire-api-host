from pydantic import BaseModel, ConfigDict, Field


class TopicEmbedded(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    topic_id: int
    order: int
    topic: str


class TopicItem(TopicEmbedded):
    course_id: int


class TopicCreate(BaseModel):
    topic: str = Field(..., min_length=1, max_length=255)


class TopicListResponse(BaseModel):
    data: list[TopicItem]


class TopicReorderRequest(BaseModel):
    topic_ids: list[int]
