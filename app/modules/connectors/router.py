from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.schemas import CurrentUser
from app.modules.connectors import service
from app.modules.partners.router import require_growth

router = APIRouter(prefix="/api/v1/cms/connectors", tags=["connectors"])


class AccountIn(BaseModel):
    platform: str
    name: str = Field(min_length=1, max_length=160)
    account_id: str | None = None
    owner: str | None = None


class LineIn(BaseModel):
    counsellor_user_id: int | None = None
    number: str
    display: str | None = None
    phone_number_id: str | None = None


@router.get("")
async def list_connectors(_: CurrentUser = Depends(require_growth), db: AsyncSession = Depends(get_db)):
    return await service.catalogue(db)


@router.get("/platforms")
async def platforms(_: CurrentUser = Depends(require_growth), db: AsyncSession = Depends(get_db)):
    data = await service.catalogue(db)
    return {"platforms": data["platforms"], "events": data["events"]}


@router.post("", status_code=201)
async def create_connector(payload: AccountIn, user: CurrentUser = Depends(require_growth), db: AsyncSession = Depends(get_db)):
    return await service.add_account(db, payload.model_dump(), user.user_id)


@router.delete("/{connector_id}", status_code=204)
async def remove_connector(connector_id: int, _: CurrentUser = Depends(require_growth), db: AsyncSession = Depends(get_db)):
    await service.delete_account(db, connector_id)


@router.get("/whatsapp-lines")
async def lines(_: CurrentUser = Depends(require_growth), db: AsyncSession = Depends(get_db)):
    data = await service.catalogue(db)
    return {"lines": data["whatsapp_lines"], "counsellors": data["counsellors"]}


@router.post("/whatsapp-lines", status_code=201)
async def create_line(payload: LineIn, user: CurrentUser = Depends(require_growth), db: AsyncSession = Depends(get_db)):
    return await service.add_line(db, payload.model_dump(), user.user_id)


@router.delete("/whatsapp-lines/{line_id}", status_code=204)
async def remove_line(line_id: int, _: CurrentUser = Depends(require_growth), db: AsyncSession = Depends(get_db)):
    await service.delete_line(db, line_id)
