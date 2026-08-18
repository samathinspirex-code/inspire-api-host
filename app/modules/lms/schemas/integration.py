from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

GoogleAccessType = Literal["open", "trusted", "restricted"]


class GoogleIntegrationUpdate(BaseModel):
    enabled: bool = False
    workspace_domain: str | None = Field(None, max_length=255)
    embed_enabled: bool = True
    calendar_sync_enabled: bool = True
    attendance_sync_enabled: bool = True
    attendance_threshold_percentage: int = Field(50, ge=1, le=100)
    default_access_type: GoogleAccessType = "restricted"

    @model_validator(mode="after")
    def validate_enabled_settings(self):
        if self.enabled and not (self.workspace_domain or "").strip():
            raise ValueError("workspace_domain is required when Google integration is enabled")
        return self


class GoogleIntegrationItem(GoogleIntegrationUpdate):
    oauth_configured: bool
    token_encryption_configured: bool
    oauth_redirect_uri: str
    setup_status: Literal[
        "disabled", "credentials_required", "security_key_required", "ready_for_account_connection"
    ]
    updated_at: datetime | None


class GoogleConnectResponse(BaseModel):
    authorization_url: str


class GoogleConnectionItem(BaseModel):
    integration_ready: bool
    connected: bool
    google_email: str | None = None
    granted_scopes: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None
    message: str
