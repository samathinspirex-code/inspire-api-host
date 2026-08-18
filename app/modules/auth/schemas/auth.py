from typing import Optional
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AuthenticatorLoginRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., pattern=r"^\d{6}$")


class AuthenticatorRecoveryRequest(BaseModel):
    email: EmailStr
    recovery_code: str = Field(..., min_length=12, max_length=20)


class AuthenticatorSetupStartRequest(BaseModel):
    email: EmailStr
    setup_token: str = Field(..., min_length=64, max_length=64)


class AuthenticatorSetupStartResponse(BaseModel):
    issuer: str
    account_email: str
    manual_key: str
    qr_code_data_url: str


class AuthenticatorSetupCompleteRequest(AuthenticatorSetupStartRequest):
    code: str = Field(..., pattern=r"^\d{6}$")


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class SsoTicketResponse(BaseModel):
    ticket: str
    expires_in: int


class ExchangeSsoTicketRequest(BaseModel):
    ticket: str = Field(..., min_length=64, max_length=64)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    email: str
    full_name: Optional[str]
    access: list[str]


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserOut


class AuthenticatorSetupCompleteResponse(TokenResponse):
    recovery_codes: list[str]


class AuthenticatorSetupTokenResponse(BaseModel):
    user_id: int
    email: str
    setup_token: str
    expires_at: datetime


class AuthenticatorInvitationResponse(AuthenticatorSetupTokenResponse):
    setup_url: str
    email_sent: bool
    delivery_message: str


class CurrentUser(BaseModel):
    user_id: int
    email: str
    access: list[str]
