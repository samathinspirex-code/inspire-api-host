from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import ForbiddenError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.partners import service

router = APIRouter(prefix="/api/v1/cms/partners", tags=["partners"])


class LinkIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    type: str
    platform: str = "all"
    programme: str | None = None
    campaign: str | None = None
    commission: float = 10000


def require_growth(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if "CMS_ADMIN" not in user.access:
        raise ForbiddenError("Requires CMS Admin access")
    return user


@router.get("/links")
async def links(_: CurrentUser = Depends(require_growth), db: AsyncSession = Depends(get_db)):
    return await service.list_links(db)


@router.get("/stats")
async def stats(_: CurrentUser = Depends(require_growth), db: AsyncSession = Depends(get_db)):
    return await service.list_links(db)


@router.post("/links", status_code=201)
async def create_link(payload: LinkIn, user: CurrentUser = Depends(require_growth), db: AsyncSession = Depends(get_db)):
    return await service.create_link(db, payload.model_dump(), user.user_id)


@router.delete("/links/{link_id}", status_code=204)
async def remove_link(link_id: int, _: CurrentUser = Depends(require_growth), db: AsyncSession = Depends(get_db)):
    await service.delete_link(db, link_id)
