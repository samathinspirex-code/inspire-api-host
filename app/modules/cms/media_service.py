import asyncio
from pathlib import Path
import re
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.cms.models import MediaAsset
from app.modules.cms.schemas.media_asset import (
    MediaAssetListResponse,
    MediaAssetResponse,
    MediaAssetUpdate,
    MediaUploadRequest,
    MediaUploadTicket,
)

ALLOWED_MEDIA_TYPES = {
    "image/jpeg": ("image", ".jpg"),
    "image/png": ("image", ".png"),
    "image/webp": ("image", ".webp"),
    "image/gif": ("image", ".gif"),
    "application/pdf": ("document", ".pdf"),
}


def _client():
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise ValidationError("The API media-storage dependency is not installed") from exc
    if not settings.MEDIA_BUCKET:
        raise ValidationError("Media storage is not configured on the API server")
    return boto3.client(
        "s3",
        region_name=settings.MEDIA_REGION,
        # Pin standard AWS S3 to its regional endpoint. Presigning against the
        # legacy global endpoint produces a 307 redirect for non-us-east-1
        # buckets, which browsers cannot follow for a signed cross-origin PUT.
        endpoint_url=settings.MEDIA_ENDPOINT_URL or f"https://s3.{settings.MEDIA_REGION}.amazonaws.com",
        aws_access_key_id=settings.MEDIA_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.MEDIA_SECRET_ACCESS_KEY or None,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def _public_url(key: str) -> str:
    encoded = "/".join(quote(part) for part in key.split("/"))
    if settings.MEDIA_PUBLIC_BASE_URL:
        return f"{settings.MEDIA_PUBLIC_BASE_URL.rstrip('/')}/{encoded}"
    if settings.MEDIA_ENDPOINT_URL:
        return f"{settings.MEDIA_ENDPOINT_URL.rstrip('/')}/{settings.MEDIA_BUCKET}/{encoded}"
    return f"https://{settings.MEDIA_BUCKET}.s3.{settings.MEDIA_REGION}.amazonaws.com/{encoded}"


def _asset_response(row: MediaAsset) -> MediaAssetResponse:
    """Build delivery URLs from the current environment, not stale DB values."""
    response = MediaAssetResponse.model_validate(row)
    if row.status == "ready":
        return response.model_copy(update={"public_url": _public_url(row.object_key)})
    return response


async def request_upload(db: AsyncSession, payload: MediaUploadRequest, user_id: int) -> MediaUploadTicket:
    media = ALLOWED_MEDIA_TYPES.get(payload.content_type.lower())
    if media is None:
        raise ValidationError("Only JPG, PNG, WebP, GIF, and PDF files are supported")
    kind, extension = media
    existing_name = await db.scalar(
        select(MediaAsset.media_asset_id).where(func.lower(MediaAsset.name) == payload.name.lower())
    )
    if existing_name is not None:
        raise ConflictError(f"Media name '{payload.name}' is already in use")
    safe_stem = re.sub(r"[^a-z0-9]+", "-", Path(payload.filename).stem.lower()).strip("-")[:80] or "asset"
    key = f"{payload.folder}/{kind}/{uuid4().hex}-{safe_stem}{extension}"
    row = MediaAsset(
        object_key=key,
        bucket=settings.MEDIA_BUCKET,
        name=payload.name,
        original_filename=payload.filename,
        content_type=payload.content_type.lower(),
        size_bytes=payload.size_bytes,
        kind=kind,
        folder=payload.folder,
        alt_text=payload.alt_text,
        status="pending",
        created_by=user_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    try:
        upload_url = await asyncio.to_thread(
            _client().generate_presigned_url,
            "put_object",
            Params={"Bucket": settings.MEDIA_BUCKET, "Key": key, "ContentType": row.content_type},
            ExpiresIn=settings.MEDIA_UPLOAD_EXPIRE_SECONDS,
        )
    except Exception as exc:
        await db.delete(row)
        await db.commit()
        raise ValidationError("Could not create a media upload URL") from exc
    return MediaUploadTicket(
        media_asset_id=row.media_asset_id,
        upload_url=upload_url,
        headers={"Content-Type": row.content_type},
        expires_in_seconds=settings.MEDIA_UPLOAD_EXPIRE_SECONDS,
    )


async def complete_upload(db: AsyncSession, asset_id: int) -> MediaAssetResponse:
    row = await db.get(MediaAsset, asset_id)
    if row is None:
        raise NotFoundError("Media asset not found")
    try:
        metadata = await asyncio.to_thread(
            _client().head_object, Bucket=row.bucket, Key=row.object_key
        )
    except Exception as exc:
        raise ValidationError("The uploaded file could not be verified") from exc
    actual_size = int(metadata.get("ContentLength") or 0)
    if actual_size <= 0 or (row.size_bytes and actual_size != row.size_bytes):
        raise ValidationError("Uploaded file size does not match the requested asset")
    row.size_bytes = actual_size
    row.public_url = _public_url(row.object_key)
    row.status = "ready"
    await db.commit()
    await db.refresh(row)
    return _asset_response(row)


async def list_assets(db: AsyncSession, kind: str | None = None) -> MediaAssetListResponse:
    stmt = select(MediaAsset).where(MediaAsset.status == "ready")
    if kind:
        stmt = stmt.where(MediaAsset.kind == kind)
    rows = (await db.execute(stmt.order_by(MediaAsset.created_at.desc()))).scalars().all()
    return MediaAssetListResponse(data=[_asset_response(row) for row in rows])


async def get_asset_by_name(db: AsyncSession, name: str) -> MediaAssetResponse:
    normalized = name.strip().lower()
    row = await db.scalar(
        select(MediaAsset).where(
            func.lower(MediaAsset.name) == normalized,
            MediaAsset.status == "ready",
        )
    )
    if row is None:
        raise NotFoundError(f"Media asset '{name}' was not found")
    return _asset_response(row)


async def update_asset(db: AsyncSession, asset_id: int, payload: MediaAssetUpdate) -> MediaAssetResponse:
    row = await db.get(MediaAsset, asset_id)
    if row is None:
        raise NotFoundError("Media asset not found")
    owner = await db.scalar(
        select(MediaAsset.media_asset_id).where(
            func.lower(MediaAsset.name) == payload.name.lower(),
            MediaAsset.media_asset_id != asset_id,
        )
    )
    if owner is not None:
        raise ConflictError(f"Media name '{payload.name}' is already in use")
    row.name = payload.name
    row.alt_text = payload.alt_text
    await db.commit()
    await db.refresh(row)
    return _asset_response(row)


async def resolve_media_reference(db: AsyncSession, value: str | None) -> str | None:
    if value is None or not value.strip():
        return value
    reference = value.strip()
    if reference.lower().startswith(("http://", "https://")):
        return reference
    asset = await get_asset_by_name(db, reference)
    if not asset.public_url:
        raise ValidationError(f"Media asset '{reference}' does not have a delivery URL")
    return asset.public_url


async def delete_asset(db: AsyncSession, asset_id: int) -> None:
    row = await db.get(MediaAsset, asset_id)
    if row is None:
        raise NotFoundError("Media asset not found")
    try:
        await asyncio.to_thread(_client().delete_object, Bucket=row.bucket, Key=row.object_key)
    except Exception as exc:
        raise ValidationError("The storage object could not be deleted") from exc
    await db.delete(row)
    await db.commit()
