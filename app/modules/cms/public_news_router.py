from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.cms import service
from app.modules.cms.schemas import NewsEventItem, PublicNewsEventListResponse

router = APIRouter(prefix="/api/v1/public/news-events", tags=["public-news-events"])


@router.get("", response_model=PublicNewsEventListResponse)
async def list_public_news_events(
    limit: int = Query(50, ge=1, le=100),
    kind: Optional[str] = Query(None, pattern="^(News|Event)$"),
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> PublicNewsEventListResponse:
    return await service.list_public_news_events(db, limit, kind, category)


@router.get("/{slug}", response_model=NewsEventItem)
async def get_public_news_event(slug: str, db: AsyncSession = Depends(get_db)) -> NewsEventItem:
    return await service.get_public_news_event(db, slug)
