from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.cms import service
from app.modules.cms.schemas import (
    PublicProgramListResponse,
    ProgramDetailWithTopicsOutcomes,
)

router = APIRouter(prefix="/api/v1/public/programs", tags=["public-programs"])


@router.get("", response_model=PublicProgramListResponse)
async def list_public_programs(db: AsyncSession = Depends(get_db)) -> PublicProgramListResponse:
    return await service.list_all_programs(db)


@router.get("/{slug}", response_model=ProgramDetailWithTopicsOutcomes)
async def get_public_program(slug: str, db: AsyncSession = Depends(get_db)) -> ProgramDetailWithTopicsOutcomes:
    return await service.get_program_by_slug(db, slug)
