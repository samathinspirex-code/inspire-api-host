import hashlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.partners import service

router = APIRouter(tags=["partner-redirect"])


@router.get("/go/{code}")
async def follow(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip = forwarded or (request.client.host if request.client else "")
    ip_hash = hashlib.sha256(f"inspire-partner:{ip}".encode()).hexdigest() if ip else None
    platform = request.query_params.get("utm_source") or request.query_params.get("platform")
    destination = await service.record_click(db, code, platform, dict(request.query_params), ip_hash)
    if not destination:
        return RedirectResponse("https://inspirecollege.lk/admissions", status_code=302)
    return RedirectResponse(destination, status_code=302)
