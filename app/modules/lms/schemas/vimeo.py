from typing import Literal

from pydantic import BaseModel, Field


class VimeoWorkspaceResponse(BaseModel):
    configured: bool
    course_id: int
    folder_uri: str | None = None
    message: str


class VimeoVideoItem(BaseModel):
    learning_item_id: int
    module_id: int
    module_title: str
    title: str
    description: str | None
    resource_url: str
    thumbnail_url: str | None = None
    duration_minutes: int | None
    status: Literal["draft", "published"]


class VimeoModuleItem(BaseModel):
    module_id: int
    title: str
    position: int


class VimeoCourseLibraryResponse(BaseModel):
    configured: bool
    course_id: int
    folder_uri: str | None = None
    modules: list[VimeoModuleItem] = []
    videos: list[VimeoVideoItem] = []


class VimeoUploadTicketRequest(BaseModel):
    module_id: int = Field(..., gt=0)
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    filename: str = Field(..., min_length=1, max_length=500)
    file_size: int = Field(..., gt=0)


class VimeoUploadTicketResponse(BaseModel):
    video_uri: str
    upload_link: str


class VimeoUploadFinalizeRequest(BaseModel):
    module_id: int = Field(..., gt=0)
    video_uri: str = Field(..., pattern=r"^/videos/\d+$")
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=5000)
    duration_minutes: int | None = Field(None, ge=1, le=10000)
    status: Literal["draft", "published"] = "draft"
    is_required: bool = True
