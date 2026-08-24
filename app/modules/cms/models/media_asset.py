from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        Index("idx_media_assets_status_created", "status", "created_at"),
        Index("idx_media_assets_name", "name", unique=True),
    )

    media_asset_id: Mapped[int] = mapped_column(primary_key=True)
    object_key: Mapped[str] = mapped_column(String(700), unique=True, nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="image")
    folder: Mapped[str] = mapped_column(String(80), nullable=False, default="media-library")
    alt_text: Mapped[str | None] = mapped_column(String(255))
    public_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
