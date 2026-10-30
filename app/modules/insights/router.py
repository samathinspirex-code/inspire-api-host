from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import ForbiddenError, NotFoundError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.crm.router import _check_lead_access
from app.modules.insights import service
from app.modules.insights.mission import save_mission
from app.modules.insights.snapshot import lead_journey

router = APIRouter(prefix="/api/v1/cms/insights", tags=["insights"])


def require_cms_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if "CMS_ADMIN" not in user.access:
        raise ForbiddenError("Requires CMS Admin access")
    return user


def require_command(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if "CMS_ADMIN" in user.access or "CRM" in user.access:
        return user
    raise ForbiddenError("Requires CMS Admin or CRM & Admissions access")


def _scope(user: CurrentUser, counsellor_id: int | None) -> int | None:
    counsellor_only = "COUNSELLOR" in user.access and not any(role in user.access for role in ("CRM", "ADMIN", "SUPER_ADMIN", "CMS_ADMIN"))
    if counsellor_only:
        return user.user_id
    return counsellor_id


@router.get("/dashboard")
async def get_dashboard(counsellor_id: int | None = None, user: CurrentUser = Depends(require_cms_admin), db: AsyncSession = Depends(get_db)):
    return await service.dashboard(db, _scope(user, counsellor_id))


@router.get("/command-centre")
async def get_command(range_days: int = Query(30, alias="range", ge=7, le=90), counsellor_id: int | None = None, user: CurrentUser = Depends(require_command), db: AsyncSession = Depends(get_db)):
    return await service.command_centre(db, _scope(user, counsellor_id), range_days)


class MissionIn(BaseModel):
    target: int = Field(ge=1, le=1_000_000)
    start: date
    end: date


@router.put("/mission")
async def put_mission(payload: MissionIn, _: CurrentUser = Depends(require_command), db: AsyncSession = Depends(get_db)):
    return await save_mission(db, payload.target, payload.start, payload.end)


@router.get("/metrics")
async def get_metrics(counsellor_id: int | None = None, user: CurrentUser = Depends(require_cms_admin), db: AsyncSession = Depends(get_db)):
    return await service.metrics(db, _scope(user, counsellor_id))


@router.get("/brain")
async def get_brain(counsellor_id: int | None = None, user: CurrentUser = Depends(require_cms_admin), db: AsyncSession = Depends(get_db)):
    return await service.brain(db, _scope(user, counsellor_id))


@router.get("/studio/{tab}")
async def get_studio(tab: str, counsellor_id: int | None = None, user: CurrentUser = Depends(require_cms_admin), db: AsyncSession = Depends(get_db)):
    allowed = {"overview", "channels", "visitors", "tracker", "champions", "lab", "connect"}
    if tab not in allowed:
        raise NotFoundError("Unknown studio tab")
    return await service.studio(db, tab, _scope(user, counsellor_id))


@router.get("/leads/{lead_id}/journey")
async def get_journey(lead_id: int, user: CurrentUser = Depends(require_cms_admin), db: AsyncSession = Depends(get_db)):
    await _check_lead_access(db, lead_id, user)
    journey = await lead_journey(db, lead_id)
    if not journey:
        raise NotFoundError("Lead not found")
    return journey
