from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.site_assistant import service
from app.modules.site_assistant.schemas import SiteAssistantChatRequest, SiteAssistantChatResponse

router = APIRouter(prefix="/api/v1/public/site-assistant", tags=["public-site-assistant"])


def _client_ip(request: Request) -> str:
    # The website proxies chat through its own server and forwards the visitor's IP.
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


@router.post("/chat", response_model=SiteAssistantChatResponse)
async def chat(payload: SiteAssistantChatRequest, request: Request, db: AsyncSession = Depends(get_db)):
    service.check_rate_limit(_client_ip(request))
    return await service.chat(db, payload)
