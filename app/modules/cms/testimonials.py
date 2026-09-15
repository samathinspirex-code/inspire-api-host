"""CMS-managed student video testimonials and their public projection."""
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import NotFoundError
from app.modules.auth.dependencies import require_access

cms_router = APIRouter(prefix="/api/v1/cms/testimonials", tags=["cms"], dependencies=[Depends(require_access("CMS"))])
public_router = APIRouter(prefix="/api/v1/public/testimonials", tags=["public-testimonials"])


class TestimonialInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    programme: str = Field(min_length=1, max_length=200)
    caption: str = Field(min_length=1, max_length=600)
    video_url: str = Field(min_length=1, max_length=2000)
    thumbnail_url: str = Field(min_length=1, max_length=2000)
    position: int = Field(default=0, ge=0, le=10000)
    status: Literal["Draft", "Published"] = "Draft"

    @field_validator("name", "programme", "caption", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("video_url", "thumbnail_url")
    @classmethod
    def media_url(cls, value):
        value = value.strip()
        if value.startswith("/testimonials/") and ".." not in value and "\\" not in value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use an HTTPS media URL or a bundled /testimonials/ asset")
        return value


@public_router.get("")
async def public_list(response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    rows = (await db.execute(text("SELECT * FROM cms_testimonials WHERE status='Published' ORDER BY position,testimonial_id"))).mappings().all()
    return {"data": [dict(row) for row in rows]}


@cms_router.get("")
async def cms_list(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(text("SELECT * FROM cms_testimonials ORDER BY position,testimonial_id"))).mappings().all()
    return {"data": [dict(row) for row in rows]}


@cms_router.post("", status_code=201)
async def create(payload: TestimonialInput, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(text("""INSERT INTO cms_testimonials(name,programme,caption,video_url,thumbnail_url,position,status)
        VALUES(:name,:programme,:caption,:video_url,:thumbnail_url,:position,:status) RETURNING *"""), payload.model_dump())).mappings().one()
    await db.commit()
    return dict(row)


@cms_router.put("/{testimonial_id}")
async def update(testimonial_id: int, payload: TestimonialInput, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(text("""UPDATE cms_testimonials SET name=:name,programme=:programme,caption=:caption,
        video_url=:video_url,thumbnail_url=:thumbnail_url,position=:position,status=:status,updated_at=now()
        WHERE testimonial_id=:id RETURNING *"""), {**payload.model_dump(), "id": testimonial_id})).mappings().first()
    if not row:
        raise NotFoundError("Testimonial not found")
    await db.commit()
    return dict(row)


@cms_router.delete("/{testimonial_id}", status_code=204)
async def delete(testimonial_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("DELETE FROM cms_testimonials WHERE testimonial_id=:id RETURNING testimonial_id"), {"id": testimonial_id})
    if result.scalar() is None:
        raise NotFoundError("Testimonial not found")
    await db.commit()
