from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GoogleIntegrationSettings(Base):
    __tablename__ = "lms_google_integration_settings"
    __table_args__ = (
        CheckConstraint("settings_id = 1", name="ck_lms_google_settings_singleton"),
        CheckConstraint(
            "default_access_type IN ('open', 'trusted', 'restricted')",
            name="ck_lms_google_settings_access_type",
        ),
    )

    settings_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    workspace_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embed_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    calendar_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    attendance_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    attendance_threshold_percentage: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=50
    )
    default_access_type: Mapped[str] = mapped_column(String(20), nullable=False, default="restricted")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GoogleOAuthState(Base):
    __tablename__ = "lms_google_oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    lecturer_user_id: Mapped[int] = mapped_column(
        ForeignKey("lms_lecturer_profiles.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GoogleAccountConnection(Base):
    __tablename__ = "lms_google_account_connections"

    lecturer_user_id: Mapped[int] = mapped_column(
        ForeignKey("lms_lecturer_profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    google_subject: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    google_email: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    granted_scopes: Mapped[str] = mapped_column(Text, nullable=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
