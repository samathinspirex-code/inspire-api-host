from typing import Literal

from pydantic import BaseModel, Field


class SiteAssistantTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=2000)


class SiteAssistantChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    path: str = Field("/", max_length=300)
    page_title: str | None = Field(None, max_length=300)
    page_text: str | None = Field(None, max_length=12000)
    history: list[SiteAssistantTurn] = Field(default_factory=list, max_length=12)


class SiteAssistantChatResponse(BaseModel):
    reply: str
