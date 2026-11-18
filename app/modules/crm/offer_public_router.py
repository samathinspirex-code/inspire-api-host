"""Public (no sign-in) endpoints behind the offer letter's "Submit your documents" link."""
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.crm import offer_service
from app.modules.payments.rate_limit import check as rate_limit

router = APIRouter(prefix="/api/v1/public/offer-documents", tags=["crm-offer-documents"])
_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


class OfferDocumentUpload(BaseModel):
    document_type: Literal["nic_passport", "qualification", "transcript"]
    filename: str = Field(..., min_length=1, max_length=255)
    content_type: Literal["application/pdf", "image/jpeg", "image/png"]
    size_bytes: int = Field(..., gt=0, le=10_485_760)
    data_base64: str = Field(..., min_length=4, max_length=14_100_000)


@router.get("/{token}")
async def view_offer(token: str, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    response.headers.update(_HEADERS)
    rate_limit(request, "offer-view", 120)
    return await offer_service.public_offer_view(db, token)


@router.post("/{token}")
async def upload_document(token: str, payload: OfferDocumentUpload, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    response.headers.update(_HEADERS)
    rate_limit(request, "offer-upload", 40)
    return await offer_service.public_upload_document(db, token, payload)
