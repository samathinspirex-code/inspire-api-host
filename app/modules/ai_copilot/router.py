import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.ai_copilot import service
from app.modules.auth.schemas import CurrentUser
from app.modules.crm.router import _check_lead_access
from app.modules.insights.router import _scope, require_cms_admin

router = APIRouter(prefix="/api/v1/cms/ai", tags=["ai-copilot"])


class Turn(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = []
    counsellor_id: int | None = None


class AnalyzeIn(BaseModel):
    dataset: str
    counsellor_id: int | None = None


class BriefIn(BaseModel):
    counsellor_id: int | None = None


class DraftIn(BaseModel):
    lead_id: int


class CaptionIn(BaseModel):
    link_id: int


@router.post("/copilot/chat")
async def chat(payload: ChatIn, user: CurrentUser = Depends(require_cms_admin), db: AsyncSession = Depends(get_db)):
    scope = _scope(user, payload.counsellor_id)

    async def generate():
        try:
            async for delta in service.chat_stream(db, user.user_id, scope, payload.message.strip(), [turn.model_dump() for turn in payload.history]):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            message = getattr(exc, "message", None) or "The copilot could not answer just now."
            yield f"data: {json.dumps({'error': message})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/analyze")
async def analyze(payload: AnalyzeIn, user: CurrentUser = Depends(require_cms_admin), db: AsyncSession = Depends(get_db)):
    return await service.analyze(db, user.user_id, _scope(user, payload.counsellor_id), payload.dataset)


@router.post("/brief")
async def brief(payload: BriefIn, user: CurrentUser = Depends(require_cms_admin), db: AsyncSession = Depends(get_db)):
    return await service.brief(db, user.user_id, _scope(user, payload.counsellor_id))


@router.post("/draft-message")
async def draft_message(payload: DraftIn, user: CurrentUser = Depends(require_cms_admin), db: AsyncSession = Depends(get_db)):
    await _check_lead_access(db, payload.lead_id, user)
    return await service.draft_message(db, user.user_id, payload.lead_id)


@router.post("/caption")
async def caption(payload: CaptionIn, user: CurrentUser = Depends(require_cms_admin), db: AsyncSession = Depends(get_db)):
    return await service.caption(db, user.user_id, payload.link_id)
